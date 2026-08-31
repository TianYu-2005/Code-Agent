from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue, ValidationError

from code_agent_core import (
    MAX_TOOL_RESULT_BYTES,
    MAX_TOOL_RESULT_CHARS,
    CompactionEntryPayload,
    ExecutionContext,
    MemoryEntryPayload,
    MessageEntryPayload,
    PermissionContext,
    RunBudgets,
    RunEventEntryPayload,
    RunSpec,
    RuntimeEvent,
    RuntimeEventType,
    SessionEntry,
    SessionEntryType,
    SessionPayload,
    ToolEffect,
    ToolOrigin,
    ToolResult,
    ToolSpec,
    ToolStatus,
)
from code_agent_llm import Message, MessageRole


class NeverCancelled:
    @property
    def is_cancelled(self) -> bool:
        return False

    async def wait(self) -> None:
        return None


class RecordingEventSink:
    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    async def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)


def test_tool_contracts_capture_policy_and_result() -> None:
    spec = ToolSpec(
        name="read_file",
        description="Read one file",
        input_schema={"type": "object"},
        effects=frozenset({ToolEffect.READ}),
        concurrency_key="path",
        origin=ToolOrigin.BUILTIN,
    )
    result = ToolResult(status=ToolStatus.SUCCESS, content="contents")

    assert spec.effects == frozenset({ToolEffect.READ})
    assert spec.timeout_seconds == 30.0
    assert result.is_success


def test_tool_contracts_reject_invalid_limits() -> None:
    with pytest.raises(ValidationError):
        ToolSpec(
            name="read_file",
            description="Read one file",
            input_schema={},
            timeout_seconds=float("inf"),
        )
    with pytest.raises(ValidationError):
        ToolResult(status=ToolStatus.SUCCESS, content="x" * (MAX_TOOL_RESULT_CHARS + 1))
    with pytest.raises(ValidationError, match=str(MAX_TOOL_RESULT_BYTES)):
        ToolResult(
            status=ToolStatus.SUCCESS,
            metadata={"oversized": "x" * MAX_TOOL_RESULT_BYTES},
        )


def test_run_spec_validates_budgets_tool_names_and_frozen_metadata() -> None:
    spec = RunSpec(
        session_id="session-1",
        model="test-model",
        tool_set=frozenset({"read_file"}),
        metadata={"labels": {"source": "test"}},
    )
    labels = cast(dict[str, JsonValue], spec.metadata["labels"])

    assert spec.agent_name == "default"
    assert spec.budgets == RunBudgets()
    with pytest.raises(ValidationError, match="frozen"):
        spec.model = "other-model"
    with pytest.raises(TypeError, match="cannot be modified"):
        labels["source"] = "changed"
    with pytest.raises(ValidationError, match="invalid tool name"):
        RunSpec(session_id="session-1", model="test-model", tool_set=frozenset({"bad name"}))
    with pytest.raises(ValidationError):
        RunBudgets(timeout_seconds=float("inf"))


def test_execution_context_requires_canonical_absolute_workspace(tmp_path: Path) -> None:
    context = ExecutionContext(
        workspace=tmp_path,
        session_id="session-1",
        run_id="run-1",
        cancellation=NeverCancelled(),
        permission_context=PermissionContext(),
        event_sink=RecordingEventSink(),
    )

    assert context.workspace == tmp_path
    attribute = "run_id"
    with pytest.raises(FrozenInstanceError):
        setattr(context, attribute, "run-2")
    with pytest.raises(ValueError, match="absolute"):
        ExecutionContext(
            workspace=Path("relative"),
            session_id="session-1",
            run_id="run-1",
            cancellation=NeverCancelled(),
            permission_context=PermissionContext(),
            event_sink=RecordingEventSink(),
        )
    with pytest.raises(ValueError, match="canonical"):
        ExecutionContext(
            workspace=tmp_path / ".." / tmp_path.name,
            session_id="session-1",
            run_id="run-1",
            cancellation=NeverCancelled(),
            permission_context=PermissionContext(),
            event_sink=RecordingEventSink(),
        )


def test_runtime_event_normalizes_timestamp_and_round_trips() -> None:
    east_of_utc = timezone(timedelta(hours=8))
    event = RuntimeEvent(
        type=RuntimeEventType.RUN_STARTED,
        timestamp=datetime(2026, 8, 31, 13, 0, tzinfo=east_of_utc),
        session_id="session-1",
        run_id="run-1",
        payload={"source": {"name": "cli"}},
    )
    source = cast(dict[str, JsonValue], event.payload["source"])

    assert event.timestamp == datetime(2026, 8, 31, 5, 0, tzinfo=UTC)
    assert RuntimeEvent.model_validate_json(event.model_dump_json()) == event
    with pytest.raises(TypeError, match="cannot be modified"):
        source["name"] = "rpc"


def test_runtime_event_rejects_invalid_timestamp_or_version() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        RuntimeEvent(
            type=RuntimeEventType.RUN_STARTED,
            timestamp=datetime(2026, 8, 31, 13, 0),
            session_id="session-1",
            run_id="run-1",
        )
    with pytest.raises(ValidationError):
        RuntimeEvent.model_validate(
            {
                "type": RuntimeEventType.RUN_STARTED,
                "schema_version": 2,
                "session_id": "session-1",
                "run_id": "run-1",
            }
        )


@pytest.mark.parametrize(
    ("event_type", "kwargs"),
    [
        (RuntimeEventType.TURN_STARTED, {}),
        (RuntimeEventType.MODEL_DELTA, {"turn_id": "turn-1"}),
        (RuntimeEventType.TOOL_COMPLETED, {"turn_id": "turn-1"}),
    ],
)
def test_runtime_event_requires_scope_correlation(
    event_type: RuntimeEventType,
    kwargs: dict[str, str],
) -> None:
    with pytest.raises(ValidationError, match="requires"):
        RuntimeEvent.model_validate(
            {
                "type": event_type,
                "session_id": "session-1",
                "run_id": "run-1",
                **kwargs,
            }
        )


def make_message() -> Message:
    return Message(id="message-1", role=MessageRole.USER, content="hello")


def test_session_entry_supports_typed_payloads_and_round_trip() -> None:
    payloads: tuple[tuple[SessionEntryType, SessionPayload], ...] = (
        (SessionEntryType.MESSAGE, MessageEntryPayload(message=make_message())),
        (
            SessionEntryType.RUN_EVENT,
            RunEventEntryPayload(
                event=RuntimeEvent(
                    type=RuntimeEventType.RUN_STARTED,
                    session_id="session-1",
                    run_id="run-1",
                )
            ),
        ),
        (
            SessionEntryType.COMPACTION,
            CompactionEntryPayload(
                summary="Earlier work",
                source_entry_ids=("entry-1",),
                branch_head_id="entry-1",
                model="test-model",
            ),
        ),
        (
            SessionEntryType.MEMORY,
            MemoryEntryPayload(memory_id="memory-1", content="Project uses Python"),
        ),
    )

    for index, (entry_type, payload) in enumerate(payloads, start=1):
        entry = SessionEntry(
            id=f"entry-{index + 1}",
            parent_id="entry-1",
            type=entry_type,
            payload=payload,
        )
        assert SessionEntry.model_validate_json(entry.model_dump_json()) == entry


def test_session_entry_rejects_invalid_tree_payload_and_version() -> None:
    with pytest.raises(ValidationError, match="own parent"):
        SessionEntry(
            id="entry-1",
            parent_id="entry-1",
            type=SessionEntryType.MESSAGE,
            payload=MessageEntryPayload(message=make_message()),
        )
    with pytest.raises(ValidationError, match="incompatible payload"):
        SessionEntry(
            id="entry-1",
            type=SessionEntryType.MESSAGE,
            payload=MemoryEntryPayload(memory_id="memory-1", content="fact"),
        )
    with pytest.raises(ValidationError):
        SessionEntry.model_validate(
            {
                "id": "entry-1",
                "type": SessionEntryType.MESSAGE,
                "payload": {"message": make_message()},
                "schema_version": 2,
            }
        )
