"""Minimal ReAct agent loop."""

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from code_agent_llm import (
    CancellationToken,
    Message,
    MessageRole,
    ModelEventType,
    ModelProvider,
    ModelProviderError,
    ProviderErrorCode,
    ToolCall,
)

from ..context.compaction import Compactor
from ..context.manager import ContextManager
from ..session.entries import (
    MessageEntryPayload,
    SessionEntry,
    SessionEntryType,
)
from ..session.store import SessionStore
from ..tools import (
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    ToolStatus,
)
from .events import RuntimeEvent, RuntimeEventType
from .spec import (
    EventSink,
    ExecutionContext,
    PermissionContext,
    RunSpec,
)


class LoopEndReason(StrEnum):
    """Why the agent loop stopped."""

    COMPLETED = "completed"
    MAX_TURNS = "max_turns"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class LoopResult:
    """Outcome of one agent loop execution."""

    end_reason: LoopEndReason
    turns: int
    final_message: Message | None = None
    error: str | None = None


@dataclass
class _TurnIds:
    """Identifiers for one model-then-tools turn."""

    turn_id: str
    model_call_id: str = ""
    tool_call_ids: dict[str, str] = field(default_factory=dict)


class AgentLoop:
    """Drive one task through model calls and tool executions."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        session: SessionStore,
        context_manager: ContextManager,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        run_spec: RunSpec,
        cancellation: CancellationToken | None = None,
        event_sink: EventSink | None = None,
        compactor: Compactor | None = None,
    ) -> None:
        self._provider = provider
        self._session = session
        self._context = context_manager
        self._registry = tool_registry
        self._executor = tool_executor
        self._run_spec = run_spec
        self._cancellation = cancellation or _NeverCancel()
        self._event_sink = event_sink or _NullSink()
        self._compactor = compactor

    async def run(self, user_message: Message) -> LoopResult:
        """Run the loop until the model finishes or a limit is reached."""
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        self._session.append(_message_entry(user_message, parent_id=self._session.current_id))
        await self._emit_event("run_started", run_id=run_id)

        turns = 0
        result: LoopResult | None = None
        while result is None:
            if self._cancellation.is_cancelled:
                result = LoopResult(
                    end_reason=LoopEndReason.CANCELLED,
                    turns=turns,
                    error="run was cancelled",
                )
                break
            if turns >= self._run_spec.budgets.max_turns:
                result = LoopResult(
                    end_reason=LoopEndReason.MAX_TURNS,
                    turns=turns,
                    error="reached the maximum number of turns",
                )
                break
            turns += 1
            result = await self._run_turn(run_id, turns)

        await self._emit_event("run_completed", run_id=run_id)
        if result is None:
            result = LoopResult(end_reason=LoopEndReason.ERROR, turns=turns)
        return result

    async def _run_turn(self, run_id: str, turn_number: int) -> LoopResult | None:
        turn = _TurnIds(turn_id=f"turn-{uuid.uuid4().hex[:12]}")
        await self._emit_event("turn_started", run_id=run_id, turn_id=turn.turn_id)

        if self._compactor is not None:
            await self._maybe_compact(run_id, turn.turn_id)

        request = self._context.build(
            self._run_spec.model,
            tools=self._registry.model_specs(self._run_spec.tool_set or None),
        )
        turn.model_call_id = f"model-{uuid.uuid4().hex[:12]}"
        await self._emit_event(
            "model_started",
            run_id=run_id,
            turn_id=turn.turn_id,
            model_call_id=turn.model_call_id,
        )

        response = None
        try:
            async for event in self._provider.stream(request, self._cancellation):
                if event.type is ModelEventType.TEXT_DELTA and event.text_delta:
                    await self._emit_event(
                        "model_delta",
                        run_id=run_id,
                        turn_id=turn.turn_id,
                        model_call_id=turn.model_call_id,
                        payload={"text": event.text_delta},
                    )
                elif event.type is ModelEventType.COMPLETED and event.response:
                    response = event.response
        except ModelProviderError as error:
            if error.code is ProviderErrorCode.CANCELLED:
                return LoopResult(
                    end_reason=LoopEndReason.CANCELLED,
                    turns=turn_number,
                    error="model request was cancelled",
                )
            return LoopResult(
                end_reason=LoopEndReason.ERROR,
                turns=turn_number,
                error=str(error),
            )

        if response is None:
            return LoopResult(
                end_reason=LoopEndReason.ERROR,
                turns=turn_number,
                error="model stream ended without a response",
            )

        assistant = Message(
            id=f"assistant-{uuid.uuid4().hex[:12]}",
            role=MessageRole.ASSISTANT,
            content=response.content,
            tool_calls=response.tool_calls,
        )
        self._session.append(_message_entry(assistant, parent_id=self._session.current_id))
        await self._emit_event(
            "model_completed",
            run_id=run_id,
            turn_id=turn.turn_id,
            model_call_id=turn.model_call_id,
        )

        if not response.tool_calls:
            await self._emit_event(
                "turn_completed",
                run_id=run_id,
                turn_id=turn.turn_id,
            )
            return LoopResult(
                end_reason=LoopEndReason.COMPLETED,
                turns=turn_number,
                final_message=assistant,
            )

        for call in response.tool_calls:
            result = await self._execute_tool_call(run_id, turn, call)
            if result is not None:
                return result

        await self._emit_event("turn_completed", run_id=run_id, turn_id=turn.turn_id)
        return None

    async def _maybe_compact(self, run_id: str, turn_id: str) -> None:
        """Run the compaction check and report the outcome as an event."""
        if self._compactor is None:
            return
        outcome = await self._compactor.maybe_compact(
            self._session,
            token_budget=self._context.policy.token_budget,
            model=self._run_spec.model,
            cancellation=self._cancellation,
        )
        if outcome.status == "skipped":
            return
        payload: dict[str, object] = {
            "status": outcome.status,
            "messages_compacted": outcome.messages_compacted,
            "tokens_before": outcome.tokens_before,
        }
        if outcome.reason:
            payload["reason"] = outcome.reason
        await self._emit_event(
            "context_compacted",
            run_id=run_id,
            turn_id=turn_id,
            payload=payload,
        )

    async def _execute_tool_call(
        self,
        run_id: str,
        turn: _TurnIds,
        call: ToolCall,
    ) -> LoopResult | None:
        tool_call_id = f"tool-{uuid.uuid4().hex[:12]}"
        turn.tool_call_ids[call.id] = tool_call_id
        await self._emit_event(
            "tool_started",
            run_id=run_id,
            turn_id=turn.turn_id,
            tool_call_id=tool_call_id,
        )

        context = ExecutionContext(
            workspace=_workspace_root(),
            session_id=self._run_spec.session_id,
            run_id=run_id,
            cancellation=self._cancellation,
            permission_context=PermissionContext(),
            event_sink=self._event_sink,
        )
        result: ToolResult = await self._executor.execute(call, context)
        tool_message = Message(
            id=f"tool-{uuid.uuid4().hex[:12]}",
            role=MessageRole.TOOL,
            content=result.content,
            tool_call_id=call.id,
        )
        self._session.append(_message_entry(tool_message, parent_id=self._session.current_id))
        await self._emit_event(
            "tool_completed",
            run_id=run_id,
            turn_id=turn.turn_id,
            tool_call_id=tool_call_id,
            payload={"status": result.status.value},
        )
        if result.status is ToolStatus.CANCELLED:
            return LoopResult(
                end_reason=LoopEndReason.CANCELLED,
                turns=0,
                error="tool execution was cancelled",
            )
        return None

    async def _emit_event(
        self,
        event_type: str,
        *,
        run_id: str,
        turn_id: str | None = None,
        model_call_id: str | None = None,
        tool_call_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> None:
        """Publish one runtime event to the configured sink."""
        event = RuntimeEvent(
            type=RuntimeEventType(event_type),
            session_id=self._run_spec.session_id,
            run_id=run_id,
            turn_id=turn_id,
            model_call_id=model_call_id,
            tool_call_id=tool_call_id,
            payload=cast(dict[str, JsonValue], payload or {}),
        )
        await self._event_sink.emit(event)


@dataclass
class _NeverCancel:
    """Cancellation token that never fires."""

    @property
    def is_cancelled(self) -> bool:
        return False

    async def wait(self) -> None:
        await asyncio.Event().wait()


class _NullSink:
    """Event sink that discards events."""

    async def emit(self, event: RuntimeEvent) -> None:
        return None


def _workspace_root() -> Path:
    return Path.cwd()


def _message_entry(message: Message, *, parent_id: str | None) -> SessionEntry:
    return SessionEntry(
        id=message.id,
        parent_id=parent_id,
        type=SessionEntryType.MESSAGE,
        payload=MessageEntryPayload(message=message),
    )
