from pathlib import Path

from code_agent_core.context import (
    ContextManager,
    ContextPolicy,
    describe_context,
    estimate_tokens,
    load_project_instructions,
)
from code_agent_core.session import MessageEntryPayload, SessionEntry, SessionEntryType
from code_agent_core.session_store import SessionStore
from code_agent_llm import Message, MessageRole, ToolCall


def user_entry(entry_id: str, parent_id: str | None, content: str) -> SessionEntry:
    return SessionEntry(
        id=entry_id,
        parent_id=parent_id,
        type=SessionEntryType.MESSAGE,
        payload=MessageEntryPayload(
            message=Message(id=entry_id, role=MessageRole.USER, content=content)
        ),
    )


def make_session(*contents: str) -> SessionStore:
    session = SessionStore()
    parent: str | None = None
    for index, content in enumerate(contents, start=1):
        entry_id = f"m{index}"
        session.append(user_entry(entry_id, parent, content))
        parent = entry_id
    return session


def test_context_builds_request_with_system_prompt_and_history() -> None:
    session = make_session("first task", "second task")
    manager = ContextManager(session)

    request = manager.build("deepseek-chat")

    assert request.model == "deepseek-chat"
    assert request.messages[0].role is MessageRole.SYSTEM
    assert "coding agent" in request.messages[0].content.lower()
    assert [message.content for message in request.messages[1:]] == [
        "first task",
        "second task",
    ]


def test_context_includes_project_instructions(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Always use ruff.", encoding="utf-8")
    session = make_session("task")
    instructions = load_project_instructions(tmp_path)
    manager = ContextManager(
        session,
        policy=ContextPolicy(project_instructions=instructions),
    )

    request = manager.build("test-model")

    assert "Always use ruff." in request.messages[1].content


def test_context_appends_extra_messages_without_mutating_session() -> None:
    session = make_session("history")
    manager = ContextManager(session)
    extra = Message(id="new-1", role=MessageRole.USER, content="extra question")

    request = manager.build("test-model", extra_messages=(extra,))

    assert request.messages[-1].content == "extra question"
    assert [item.id for item in session.current_path()] == ["m1"]


def test_context_trims_old_messages_when_over_budget() -> None:
    session = make_session("a" * 400, "b" * 400, "c" * 400, "d" * 400)
    manager = ContextManager(session, policy=ContextPolicy(token_budget=300))

    request = manager.build("test-model")

    contents = [message.content for message in request.messages[1:]]
    assert "d" * 400 in contents
    assert "a" * 400 not in contents
    view = describe_context(manager)
    assert view.truncated is True
    assert view.included_messages < view.total_messages


def test_context_keeps_tool_result_paired_with_call() -> None:
    session = make_session("start")
    session.append(
        SessionEntry(
            id="assistant-1",
            parent_id="m1",
            type=SessionEntryType.MESSAGE,
            payload=MessageEntryPayload(
                message=Message(
                    id="assistant-1",
                    role=MessageRole.ASSISTANT,
                    content="",
                    tool_calls=(ToolCall(id="call-1", name="read_file", arguments_json="{}"),),
                )
            ),
        )
    )
    session.append(
        SessionEntry(
            id="tool-1",
            parent_id="assistant-1",
            type=SessionEntryType.MESSAGE,
            payload=MessageEntryPayload(
                message=Message(
                    id="tool-1",
                    role=MessageRole.TOOL,
                    content="file content",
                    tool_call_id="call-1",
                )
            ),
        )
    )
    manager = ContextManager(session, policy=ContextPolicy(token_budget=10_000))

    request = manager.build("test-model")

    tail = request.messages[-2:]
    assert tail[0].role is MessageRole.ASSISTANT
    assert tail[1].role is MessageRole.TOOL
    assert tail[1].tool_call_id == "call-1"


def test_estimate_tokens_uses_content_and_tool_arguments() -> None:
    plain = Message(id="m1", role=MessageRole.USER, content="x" * 400)
    with_tools = Message(
        id="m2",
        role=MessageRole.ASSISTANT,
        tool_calls=(ToolCall(id="c1", name="run", arguments_json="y" * 200),),
    )

    assert estimate_tokens(plain) == 100
    assert estimate_tokens(with_tools) == 50


def test_missing_agents_md_returns_none(tmp_path: Path) -> None:
    assert load_project_instructions(tmp_path) is None
