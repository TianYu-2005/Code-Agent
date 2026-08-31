from pathlib import Path

import pytest
from pydantic import ValidationError

from code_agent_core import RuntimeEvent, RuntimeEventType
from code_agent_core.session import (
    MessageEntryPayload,
    RunEventEntryPayload,
    SessionEntry,
    SessionEntryType,
    SessionError,
    SessionFileStore,
    SessionStore,
)
from code_agent_llm import Message, MessageRole


def user_message(message_id: str, content: str) -> MessageEntryPayload:
    return MessageEntryPayload(
        message=Message(id=message_id, role=MessageRole.USER, content=content)
    )


def entry(entry_id: str, parent_id: str | None, payload: MessageEntryPayload) -> SessionEntry:
    return SessionEntry(
        id=entry_id,
        parent_id=parent_id,
        type=SessionEntryType.MESSAGE,
        payload=payload,
    )


def test_linear_session_append_and_current_path() -> None:
    session = SessionStore()
    session.append(entry("m1", None, user_message("m1", "hello")))
    session.append(entry("m2", "m1", user_message("m2", "run tests")))
    session.append(entry("m3", "m2", user_message("m3", "done")))

    path = session.current_path()

    assert [item.id for item in path] == ["m1", "m2", "m3"]
    assert session.current_id == "m3"
    assert [payload.message.content for payload in session.messages()] == [
        "hello",
        "run tests",
        "done",
    ]


def test_run_event_does_not_advance_head() -> None:
    session = SessionStore()
    session.append(entry("m1", None, user_message("m1", "task")))
    session.append_event(
        RunEventEntryPayload(
            event=RuntimeEvent(
                type=RuntimeEventType.TOOL_COMPLETED,
                session_id="session-1",
                run_id="run-1",
                turn_id="turn-1",
                tool_call_id="call-1",
            )
        )
    )

    assert session.current_id == "m1"
    assert len(session.current_path()) == 1


def test_rewind_moves_head_back() -> None:
    session = SessionStore()
    session.append(entry("m1", None, user_message("m1", "a")))
    session.append(entry("m2", "m1", user_message("m2", "b")))
    session.append(entry("m3", "m2", user_message("m3", "c")))

    session.rewind("m1")

    assert session.current_id == "m1"
    assert [item.id for item in session.current_path()] == ["m1"]
    with pytest.raises(SessionError, match="non-branch"):
        session.rewind("m3")


def test_fork_creates_new_branch() -> None:
    session = SessionStore()
    session.append(entry("m1", None, user_message("m1", "a")))
    session.append(entry("m2", "m1", user_message("m2", "b")))
    session.fork("m1")
    session.append(entry("m3", "m1", user_message("m3", "new branch")))

    assert [item.id for item in session.current_path()] == ["m1", "m3"]
    session.fork("m2")
    assert [item.id for item in session.current_path()] == ["m1", "m2"]


def test_append_rejects_duplicate_ids_and_unknown_parents() -> None:
    session = SessionStore()
    session.append(entry("m1", None, user_message("m1", "a")))

    with pytest.raises(SessionError, match="duplicate"):
        session.append(entry("m1", None, user_message("m1", "dup")))
    with pytest.raises(SessionError, match="unknown parent"):
        session.append(entry("m2", "missing", user_message("m2", "b")))
    with pytest.raises(SessionError, match="root"):
        session.append(entry("m2", None, user_message("m2", "second root")))


def test_fork_requires_message_entries() -> None:
    session = SessionStore()
    session.append(entry("m1", None, user_message("m1", "a")))
    event_entry = session.append_event(
        RunEventEntryPayload(
            event=RuntimeEvent(
                type=RuntimeEventType.RUN_STARTED,
                session_id="session-1",
                run_id="run-1",
            )
        )
    )

    with pytest.raises(SessionError, match="message"):
        session.fork(event_entry.id)


def test_list_branches_reports_heads() -> None:
    session = SessionStore()
    session.append(entry("m1", None, user_message("m1", "a")))
    session.append(entry("m2", "m1", user_message("m2", "b")))
    session.fork("m1")
    session.append(entry("m3", "m1", user_message("m3", "new")))

    branches = session.list_branches()

    head_ids = {branch.head_id for branch in branches}
    assert head_ids == {"m2", "m3"}
    counts = {branch.head_id: branch.message_count for branch in branches}
    assert counts == {"m2": 2, "m3": 2}


def test_recovered_session_can_continue_conversation(tmp_path: Path) -> None:
    journal = tmp_path / "session.jsonl"
    first = SessionFileStore(journal)
    first.append(entry("m1", None, user_message("m1", "hello")))
    first.append(entry("m2", "m1", user_message("m2", "run tests")))

    restored = SessionFileStore(journal)
    restored.append(entry("m3", "m2", user_message("m3", "continue")))

    assert [item.id for item in restored.current_path()] == ["m1", "m2", "m3"]
    again = SessionFileStore(journal)
    assert [item.id for item in again.current_path()] == ["m1", "m2", "m3"]


def test_file_store_round_trip_and_recovery(tmp_path: Path) -> None:
    journal = tmp_path / "session.jsonl"
    session = SessionFileStore(journal)
    session.append(entry("m1", None, user_message("m1", "hello")))
    session.append(entry("m2", "m1", user_message("m2", "continue")))

    restored = SessionFileStore(journal)
    assert [item.id for item in restored.current_path()] == ["m1", "m2"]
    assert restored.current_id == "m2"


def test_file_store_truncates_corrupt_tail(tmp_path: Path) -> None:
    journal = tmp_path / "session.jsonl"
    session = SessionFileStore(journal)
    session.append(entry("m1", None, user_message("m1", "hello")))
    with journal.open("a", encoding="utf-8") as handle:
        handle.write('{"id": "broken"')

    restored = SessionFileStore(journal)

    assert [item.id for item in restored.current_path()] == ["m1"]
    with journal.open("r", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip()]
    assert len(lines) == 1
    with pytest.raises(ValidationError):
        SessionEntry.model_validate({"id": "broken"})


def test_file_store_persists_rewound_head(tmp_path: Path) -> None:
    journal = tmp_path / "session.jsonl"
    session = SessionFileStore(journal)
    session.append(entry("m1", None, user_message("m1", "hello")))
    session.append(entry("m2", "m1", user_message("m2", "run tests")))
    session.append(entry("m3", "m2", user_message("m3", "more")))

    session.rewind("m2")

    restored = SessionFileStore(journal)
    assert restored.current_id == "m2"
    assert [item.id for item in restored.current_path()] == ["m1", "m2"]


def test_file_store_head_follows_new_messages_after_rewind(tmp_path: Path) -> None:
    journal = tmp_path / "session.jsonl"
    session = SessionFileStore(journal)
    session.append(entry("m1", None, user_message("m1", "hello")))
    session.append(entry("m2", "m1", user_message("m2", "run tests")))

    session.rewind("m1")
    session.append(entry("m3", "m1", user_message("m3", "new branch")))

    restored = SessionFileStore(journal)
    assert restored.current_id == "m3"
    assert [item.id for item in restored.current_path()] == ["m1", "m3"]


def test_file_store_persists_forked_head(tmp_path: Path) -> None:
    journal = tmp_path / "session.jsonl"
    session = SessionFileStore(journal)
    session.append(entry("m1", None, user_message("m1", "hello")))
    session.append(entry("m2", "m1", user_message("m2", "run tests")))

    session.fork("m1")

    restored = SessionFileStore(journal)
    assert restored.current_id == "m1"
