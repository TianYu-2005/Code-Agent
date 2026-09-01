"""End-to-end smoke tests driving the TUI like a human operator."""

import asyncio
import time
from pathlib import Path
from typing import Any, cast

import pytest
from textual.widgets import Input, Static

from code_agent.tui.app import CodeAgentApp
from code_agent.tui.renderer import TuiApprovalPort, TuiRenderer


def _event(kind: str, payload: Any = None) -> Any:
    from code_agent_llm import ModelEvent, ModelEventType

    types = {
        "text_delta": ModelEventType.TEXT_DELTA,
        "completed": ModelEventType.COMPLETED,
    }
    kwargs: dict[str, Any] = {}
    if kind == "text_delta":
        kwargs["text_delta"] = payload
    elif kind == "completed":
        kwargs["response"] = payload
    return ModelEvent(type=types[kind], **kwargs)


def _final(text: str) -> list[Any]:
    from code_agent_llm import FinishReason, ModelResponse

    return [_event("completed", ModelResponse(content=text, finish_reason=FinishReason.STOP))]


def _tool_call(name: str, arguments: str, call_id: str = "call-1", content: str = "") -> list[Any]:
    from code_agent_llm import FinishReason, ModelResponse, ToolCall

    return [
        _event(
            "completed",
            ModelResponse(
                content=content,
                tool_calls=(ToolCall(id=call_id, name=name, arguments_json=arguments),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
        )
    ]


def _build_app(
    tmp_path: Path,
    scripts: list[Any],
    *,
    token_budget: int | None = None,
) -> CodeAgentApp:
    """Assemble a complete TUI app around a fake provider inside tmp_path."""
    from code_agent.bootstrap import AgentRuntime
    from code_agent.config import load_config
    from code_agent_core.context import ContextPolicy
    from code_agent_llm import FakeProvider

    provider = FakeProvider(scripts)
    config = load_config(workspace=str(tmp_path))
    app = CodeAgentApp()
    kwargs: dict[str, Any] = {
        "provider": provider,
        "approval_port": TuiApprovalPort(app),
        "event_sink": TuiRenderer(app),
    }
    if token_budget is not None:
        kwargs["context_policy"] = ContextPolicy(token_budget=token_budget)
    runtime = AgentRuntime(config, **kwargs)
    app.bind_runtime(runtime)
    return app


def _log_text(app: CodeAgentApp) -> str:
    from textual.widgets import RichLog

    log = app.query_one("#transcript", RichLog)
    return "\n".join("".join(segment.text for segment in line) for line in log.lines)


async def _wait_for(predicate: Any, timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met within timeout")


async def _submit(app: CodeAgentApp, pilot: Any, text: str) -> None:
    prompt = app.query_one("#prompt", Input)
    prompt.value = text
    await pilot.press("enter")


async def _drive(app: CodeAgentApp, actions: Any) -> None:
    async with app.run_test(size=(100, 34)) as pilot:
        await actions(pilot)


# --------------------------------------------------------------------------
# Scenario 1: full user journey — task, approval, restart, resume, continue.
# --------------------------------------------------------------------------


def test_journey_task_approval_persist_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    first_scripts = [
        _tool_call("write_file", '{"path": "hello.txt", "content": "hello e2e"}', content="我来写"),
        _final("已创建 hello.txt"),
    ]

    async def first_session(pilot: Any) -> None:
        await _submit(app, pilot, "写一个 hello.txt")
        await _wait_for(lambda: app._pending_approval is not None)
        assert "write_file" in str(app.query_one("#approval", Static).content)
        await pilot.press("y")
        await _wait_for(lambda: not app._task_running and "已创建" in _log_text(app))
        assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "hello e2e"
        log = _log_text(app)
        assert "❯ 写一个 hello.txt" in log
        assert "Agent" in log and "已创建 hello.txt" in log

    app = _build_app(tmp_path, first_scripts)
    asyncio.run(_drive(app, first_session))

    # --- Simulate a full restart: brand-new app + runtime in the same workspace.
    second_scripts = [_final("继续没问题")]

    async def second_session(pilot: Any) -> None:
        await _submit(app2, pilot, "/sessions")
        await pilot.pause()
        listing = _log_text(app2)
        assert "写一个 hello.txt" in listing  # session title from first message

        await _submit(app2, pilot, "/sessions 1")
        await pilot.pause()
        assert "已恢复会话" in _log_text(app2)

        # The restored history must reach the model on the next request.
        await _submit(app2, pilot, "继续")
        await _wait_for(lambda: not app2._task_running and "继续没问题" in _log_text(app2))
        assert "Agent" in _log_text(app2) and "继续没问题" in _log_text(app2)

        from code_agent_llm import FakeProvider

        assert app2._runtime is not None
        provider = cast(FakeProvider, app2._runtime.provider)
        last_request = provider.requests[-1]
        history = [message.content or "" for message in last_request.messages]
        assert any("写一个 hello.txt" in content for content in history)
        assert any("继续" in content for content in history)

    app2 = _build_app(tmp_path, second_scripts)
    asyncio.run(_drive(app2, second_session))

    # Resumed conversation is persisted on the same session file.
    files = list((tmp_path / ".code-agent" / "sessions").glob("*.jsonl"))
    assert len(files) == 1


# --------------------------------------------------------------------------
# Scenario 2: auto-compaction mid-conversation, then rewind to fork.
# --------------------------------------------------------------------------


def test_journey_compaction_and_rewind(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent_llm import Message, MessageRole

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    app = _build_app(
        tmp_path,
        [
            _final("任务摘要：整理并修复登录"),  # compaction summary
            _final("第一轮完成"),  # answer to the new task
        ],
        token_budget=400,
    )

    # Preload a long history on the session before driving the UI.
    session = app._runtime.session  # type: ignore[union-attr]
    for entry_id, role, content in [
        ("h1", MessageRole.USER, "整理项目结构 " + "细节" * 200),
        ("h2", MessageRole.ASSISTANT, "好的 " + "分析" * 200),
        ("h3", MessageRole.USER, "修复登录空指针 " + "上下文" * 200),
        ("h4", MessageRole.ASSISTANT, "已修复 " + "说明" * 200),
        ("h5", MessageRole.USER, "跑一下测试"),
    ]:
        from code_agent_core.session.entries import MessageEntryPayload
        from code_agent_core.session.store import SessionStore

        SessionStore.append_message(
            session,
            entry_id,
            MessageEntryPayload(message=Message(id=entry_id, role=role, content=content)),
        )

    async def drive(pilot: Any) -> None:
        await _submit(app, pilot, "现在总结一下")
        await _wait_for(
            lambda: (
                not app._task_running
                and "第一轮完成" in _log_text(app)
                and "上下文已自动压缩" in _log_text(app)
            )
        )
        # The summary itself only goes to the model, not the transcript;
        # the transcript shows the ◇ compaction notice instead.
        assert "◇ 上下文已自动压缩（4 条历史消息转为摘要）" in _log_text(app)

        # The model request carried the summary instead of the old history.
        from code_agent_llm import FakeProvider

        assert app._runtime is not None
        provider = cast(FakeProvider, app._runtime.provider)
        final_request = provider.requests[-1]
        contents = [message.content or "" for message in final_request.messages]
        assert any("任务摘要" in content for content in contents)
        assert not any("整理项目结构" in content for content in contents)

        # Rewind past this task's two messages and fork from the old branch.
        await _submit(app, pilot, "/rewind 2")
        await pilot.pause()
        assert "已回退 2 条消息" in _log_text(app)
        assert session.current_path()[-1].id == "h5"

    asyncio.run(_drive(app, drive))


# --------------------------------------------------------------------------
# Scenario 3: failure paths — denied approval, model error, Ctrl+C cancel.
# --------------------------------------------------------------------------


def test_journey_denied_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    app = _build_app(
        tmp_path,
        [
            _tool_call("write_file", '{"path": "nope.txt", "content": "x"}'),
            _final("好的，那我换个方式"),
        ],
    )

    async def drive(pilot: Any) -> None:
        await _submit(app, pilot, "写 nope.txt")
        await _wait_for(lambda: app._pending_approval is not None)
        await pilot.press("n")
        await _wait_for(lambda: not app._task_running and "换个方式" in _log_text(app))
        assert not (tmp_path / "nope.txt").exists()
        assert "denied" in _log_text(app)

    asyncio.run(_drive(app, drive))


def test_journey_model_error_then_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent_llm import ModelProviderError, ProviderErrorCode

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    app = _build_app(
        tmp_path,
        [
            ModelProviderError(ProviderErrorCode.AUTHENTICATION, "auth failed"),
            _final("恢复了"),
        ],
    )

    async def drive(pilot: Any) -> None:
        await _submit(app, pilot, "第一问")
        await _wait_for(lambda: not app._task_running and "出错" in _log_text(app))
        assert app._task_running is False
        # The UI stays usable: the next task works.
        await _submit(app, pilot, "第二问")
        await _wait_for(lambda: not app._task_running and "恢复了" in _log_text(app))
        assert "Agent" in _log_text(app) and "恢复了" in _log_text(app)

    asyncio.run(_drive(app, drive))


def test_journey_ctrl_c_cancels_running_task(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent_llm import FakeProvider, FinishReason, ModelResponse

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    release = asyncio.Event()

    class GatedProvider(FakeProvider):
        """Provider that blocks until released or cancelled."""

        async def stream(self, request: Any, cancellation: Any) -> Any:
            from code_agent_llm import ModelProviderError, ProviderErrorCode

            while not release.is_set():
                if cancellation.is_cancelled:
                    raise ModelProviderError(
                        ProviderErrorCode.CANCELLED, "model request was cancelled"
                    )
                await asyncio.sleep(0.02)
            async for event in super().stream(request, cancellation):
                yield event

    provider = GatedProvider(
        [
            [
                _event(
                    "completed",
                    ModelResponse(content="不该到达", finish_reason=FinishReason.STOP),
                )
            ]
        ]
    )
    from code_agent.bootstrap import AgentRuntime
    from code_agent.config import load_config

    config = load_config(workspace=str(tmp_path))
    app = CodeAgentApp()
    runtime = AgentRuntime(
        config,
        provider=provider,
        approval_port=TuiApprovalPort(app),
        event_sink=TuiRenderer(app),
    )
    app.bind_runtime(runtime)

    async def drive(pilot: Any) -> None:
        await _submit(app, pilot, "慢任务")
        await pilot.pause(0.2)
        assert app._task_running is True
        await pilot.press("ctrl+c")
        await pilot.pause(0.3)
        assert "已取消" in _log_text(app)
        assert app._task_running is False

    asyncio.run(_drive(app, drive))
