"""Tests for workspace session persistence in the CLI."""

import asyncio
import io
from pathlib import Path

import pytest

from code_agent.cli.renderer import TerminalRenderer
from code_agent.cli.sessions import SessionManager, SessionManagerError
from code_agent_core.session.store import SessionStore
from code_agent_llm import Message, MessageRole


def _message(message_id: str, role: MessageRole, content: str) -> Message:
    return Message(id=message_id, role=role, content=content)


def _append(store: SessionStore, message_id: str, role: MessageRole, content: str) -> None:
    from code_agent_core.session.entries import MessageEntryPayload

    SessionStore.append_message(
        store, message_id, MessageEntryPayload(message=_message(message_id, role, content))
    )


def test_create_persists_journal_and_gitignore(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    store = manager.create()

    assert store.path.parent == tmp_path / ".code-agent" / "sessions"
    assert store.path.is_file()
    assert store.current_id is None
    assert (tmp_path / ".code-agent" / ".gitignore").read_text(encoding="utf-8") == "sessions/\n"


def test_list_resume_and_export_roundtrip(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    store = manager.create()
    _append(store, "m1", MessageRole.USER, "修复登录空指针\n以及超时问题")
    _append(store, "m2", MessageRole.ASSISTANT, "好的，我先看一下。")

    sessions = manager.list_sessions()
    assert len(sessions) == 1
    summary = sessions[0]
    assert summary.session_id == store.session_id
    assert summary.message_count == 2
    assert "修复登录空指针" in summary.title

    resumed = manager.load(summary.session_id)
    assert [p.message.content for p in resumed.messages()] == [
        "修复登录空指针\n以及超时问题",
        "好的，我先看一下。",
    ]

    target = manager.export_markdown(summary.session_id)
    exported = target.read_text(encoding="utf-8")
    assert f"# 会话 {summary.session_id}" in exported
    assert "修复登录空指针" in exported
    assert "## assistant" in exported


def test_empty_sessions_are_hidden_and_removable(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)
    empty = manager.create()
    used = manager.create()
    _append(used, "m1", MessageRole.USER, "hi")

    assert manager.list_sessions()[0].session_id == used.session_id

    manager.remove_if_empty(empty)
    manager.remove_if_empty(used)  # non-empty must survive
    assert not empty.path.exists()
    assert used.path.exists()


def test_load_rejects_invalid_identifiers(tmp_path: Path) -> None:
    manager = SessionManager(tmp_path)

    with pytest.raises(SessionManagerError):
        manager.load("../escape")
    with pytest.raises(SessionManagerError):
        manager.load("missing-session")


def test_runtime_binds_and_switches_sessions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent.bootstrap import AgentRuntime
    from code_agent.config import load_config
    from code_agent_llm import FakeProvider, FinishReason, ModelResponse

    provider = FakeProvider.from_responses(
        [ModelResponse(content="pong", tool_calls=(), finish_reason=FinishReason.STOP)]
    )
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")
    config = load_config(workspace=str(tmp_path))
    runtime = AgentRuntime(config, provider=provider)

    first_id = runtime.run_spec.session_id
    assert first_id != "default"
    assert (tmp_path / ".code-agent" / "sessions" / f"{first_id}.jsonl").is_file()

    loop = runtime.make_loop()
    message = Message(id="u1", role=MessageRole.USER, content="ping")
    result = asyncio.run(loop.run(message))
    assert result.end_reason.value == "completed"

    runtime.new_session()
    second_id = runtime.run_spec.session_id
    assert second_id != first_id
    # The previous session had messages, so it is listed and persisted.
    listed = {item.session_id for item in runtime.session_manager.list_sessions()}
    assert first_id in listed

    runtime.load_session(first_id)
    assert runtime.run_spec.session_id == first_id
    contents = [p.message.content for p in runtime.session.messages()]
    assert "ping" in contents
    assert "pong" in contents


def test_rewind_and_fork_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent.bootstrap import AgentRuntime
    from code_agent.cli.app import _handle_fork, _handle_rewind
    from code_agent.config import load_config
    from code_agent_llm import FakeProvider, FinishReason, ModelResponse

    provider = FakeProvider.from_responses(
        [ModelResponse(content="ok", tool_calls=(), finish_reason=FinishReason.STOP)]
    )
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")
    config = load_config(workspace=str(tmp_path))
    output = io.StringIO()
    runtime = AgentRuntime(
        config,
        provider=provider,
        renderer=TerminalRenderer(output_stream=output),
    )

    _append(runtime.session, "m1", MessageRole.USER, "first question")
    _append(runtime.session, "m2", MessageRole.ASSISTANT, "first answer")
    _append(runtime.session, "m3", MessageRole.USER, "second question")
    _append(runtime.session, "m4", MessageRole.ASSISTANT, "second answer")

    _handle_rewind(runtime, "2")
    assert runtime.session.current_id == "m2"
    assert "已回退" in output.getvalue()

    # Continuing from the rewound point creates a new branch on the tree.
    _append(runtime.session, "m5", MessageRole.USER, "new direction")
    branches = runtime.session.list_branches()
    assert len(branches) >= 2

    _handle_fork(runtime, "1")
    assert runtime.session.current_id == branches[0].head_id

    _handle_rewind(runtime, "0")
    assert "需在 1-3 之间" in output.getvalue()
    _handle_rewind(runtime, "abc")
    assert "最近消息" in output.getvalue()
