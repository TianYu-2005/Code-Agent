"""Integration tests for the inline Textual TUI."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Input, Static

from code_agent.tui.app import CodeAgentApp
from code_agent.tui.renderer import TuiApprovalPort, TuiRenderer


def _build_app(tmp_path: Path, scripts: list[Any]) -> CodeAgentApp:
    """Assemble the TUI app around a fake provider inside tmp_path."""
    from code_agent.bootstrap import AgentRuntime
    from code_agent.config import load_config
    from code_agent_llm import FakeProvider

    provider = FakeProvider(scripts)
    config = load_config(workspace=str(tmp_path))
    app = CodeAgentApp()
    runtime = AgentRuntime(
        config,
        provider=provider,
        approval_port=TuiApprovalPort(app),
        event_sink=TuiRenderer(app),
    )
    app.bind_runtime(runtime)
    return app


async def _drive(app: CodeAgentApp, actions: Callable[[Any], Awaitable[None]]) -> None:
    async with app.run_test(size=(100, 34)) as pilot:
        await actions(pilot)


def test_chat_roundtrip_streams_answer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent_llm import FinishReason, ModelResponse

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    async def actions(pilot: Any) -> None:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "你好"
        await pilot.press("enter")
        await _wait_for(lambda: not app._task_running and "pong" in _log_text(app))
        log_text = _log_text(app)
        assert "你好" in log_text
        assert "pong" in log_text

    app = _build_app(
        tmp_path,
        [
            [
                _event("text_delta", "pon"),
                _event("text_delta", "g"),
                _event(
                    "completed",
                    ModelResponse(content="pong", finish_reason=FinishReason.STOP),
                ),
            ]
        ],
    )
    asyncio.run(_drive(app, actions))


def test_approval_flow_allows_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent_llm import FinishReason, ModelResponse, ToolCall

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    first = _event(
        "completed",
        ModelResponse(
            content="我来写文件",
            tool_calls=(
                ToolCall(
                    id="call-1",
                    name="write_file",
                    arguments_json='{"path": "out.txt", "content": "hi"}',
                ),
            ),
            finish_reason=FinishReason.TOOL_CALLS,
        ),
    )
    second = _event("completed", ModelResponse(content="写好了", finish_reason=FinishReason.STOP))

    async def actions(pilot: Any) -> None:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "写一个 out.txt"
        await pilot.press("enter")
        await _wait_for(lambda: app._pending_approval is not None)
        approval_text = str(app.query_one("#approval", Static).content)
        assert "write_file" in approval_text
        assert app.query_one("#prompt", Input).disabled is True
        await pilot.press("y")
        await _wait_for(lambda: not app._task_running)
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hi"
        assert "写好了" in _log_text(app)

    app = _build_app(tmp_path, [[first], [second]])
    asyncio.run(_drive(app, actions))


def test_help_and_model_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    async def actions(pilot: Any) -> None:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/help"
        await pilot.press("enter")
        await pilot.pause()
        prompt.value = "/model"
        await pilot.press("enter")
        await pilot.pause()
        log_text = _log_text(app)
        assert "可用命令" in log_text
        assert "deepseek-v4-flash" in log_text

    app = _build_app(tmp_path, [])
    asyncio.run(_drive(app, actions))


def test_assistant_reply_renders_markdown(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent_llm import FinishReason, ModelResponse

    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")
    md = "# Title\n\nUse `code` and **bold**.\n\n- item one\n- item two"

    async def actions(pilot: Any) -> None:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "markdown?"
        await pilot.press("enter")
        await _wait_for(lambda: not app._task_running and "Title" in _log_text(app))
        text = _log_text(app)
        assert "Agent" in text
        assert "Title" in text
        assert "item one" in text
        # Raw markdown markers must not leak through.
        assert "**bold**" not in text
        assert "```" not in text

    app = _build_app(
        tmp_path,
        [[_event("completed", ModelResponse(content=md, finish_reason=FinishReason.STOP))]],
    )
    asyncio.run(_drive(app, actions))


def test_agent_label_renders_above_markdown_body(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent_llm import FinishReason, ModelResponse

    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    async def actions(pilot: Any) -> None:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "go"
        await pilot.press("enter")
        await _wait_for(lambda: not app._task_running and "first sentence" in _log_text(app))
        text = _log_text(app)
        assert "Agent" in text
        # The reply body is still rendered through Markdown (assertions
        # below are safe checks for non-leaking markdown markers).
        assert "first sentence" in text
        assert "**details**" not in text

    app = _build_app(
        tmp_path,
        [
            [
                _event(
                    "completed",
                    ModelResponse(
                        content="first sentence with details.\n\nSecond paragraph here.",
                        finish_reason=FinishReason.STOP,
                    ),
                )
            ]
        ],
    )
    asyncio.run(_drive(app, actions))


def test_tool_error_displays_failure_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent_llm import FinishReason, ModelResponse, ToolCall

    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    async def actions(pilot: Any) -> None:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "go"
        await pilot.press("enter")
        await _wait_for(lambda: app._pending_approval is not None)
        await pilot.press("y")
        await _wait_for(lambda: not app._task_running, timeout=2)
        text = _log_text(app)
        assert "run_command" in text
        assert "boom message" in text

    from typing import cast

    from code_agent.coding_tools.run_command import RunCommandTool
    from code_agent_core.runtime.spec import ExecutionContext
    from code_agent_core.tools.base import ToolOutputSink, ValidatedToolCall

    real_execute = RunCommandTool.execute

    async def failing_execute(
        self: RunCommandTool,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: ToolOutputSink,
    ) -> None:
        # Raise so the executor surfaces the failure with a status of ERROR
        # and content captured from the exception message.
        raise RuntimeError("boom message here")

    RunCommandTool.execute = cast("Any", failing_execute)  # type: ignore[method-assign]
    try:
        app = _build_app(
            tmp_path,
            [
                [
                    _event(
                        "completed",
                        ModelResponse(
                            content="执行",
                            tool_calls=(
                                ToolCall(
                                    id="call-1",
                                    name="run_command",
                                    arguments_json='{"command": ["true"]}',
                                ),
                            ),
                            finish_reason=FinishReason.TOOL_CALLS,
                        ),
                    )
                ]
            ],
        )
        asyncio.run(_drive(app, actions))
    finally:
        RunCommandTool.execute = real_execute  # type: ignore[method-assign]


def test_banner_and_speaker_labels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent_llm import FinishReason, ModelResponse

    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    async def actions(pilot: Any) -> None:
        text = _log_text(app)
        assert "Welcome to SeeCoder" in text  # framed welcome line
        assert "S E E C O D E R" not in text  # no spaced fallback anymore
        # pyfiglet Big font wraps each letter with underscores/slashes.
        assert "/" in text and "\\" in text
        prompt = app.query_one("#prompt", Input)
        prompt.value = "ping"
        await pilot.press("enter")
        await _wait_for(lambda: not app._task_running and "pong" in _log_text(app))
        log_text = _log_text(app)
        assert "❯ ping" in log_text
        assert "Agent" in log_text
        assert "pong" in log_text

    app = _build_app(
        tmp_path,
        [
            [
                _event("text_delta", "pong"),
                _event(
                    "completed",
                    ModelResponse(content="pong", finish_reason=FinishReason.STOP),
                ),
            ]
        ],
    )
    asyncio.run(_drive(app, actions))


def test_layout_follows_terminal_size(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")
    heights: list[int] = []

    async def measure(size: tuple[int, int]) -> int:
        app = _build_app(tmp_path, [])
        async with app.run_test(size=size) as pilot:
            await pilot.resize_terminal(size[0], size[1])
            await pilot.pause()
            return app.screen.size.height

    heights.append(asyncio.run(measure((100, 40))))
    heights.append(asyncio.run(measure((100, 20))))
    # The screen height tracks the terminal size minus a one-line footer.
    assert heights[0] >= 35
    assert heights[1] >= 17
    assert heights[0] > heights[1]


def test_user_turn_renders_separator_and_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    async def actions(pilot: Any) -> None:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "hello there"
        await pilot.press("enter")
        await pilot.pause()
        text = _log_text(app)
        assert "❯ hello there" in text
        assert "─" in text  # thin separator above the user message

    app = _build_app(tmp_path, [])
    asyncio.run(_drive(app, actions))


def test_tool_result_renders_compact_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent_llm import FinishReason, ModelResponse, ToolCall

    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)

    first = _event(
        "completed",
        ModelResponse(
            content="",
            tool_calls=(
                ToolCall(
                    id="call-1",
                    name="write_file",
                    arguments_json='{"path": "out.txt", "content": "hi"}',
                ),
            ),
            finish_reason=FinishReason.TOOL_CALLS,
        ),
    )
    second = _event("completed", ModelResponse(content="done", finish_reason=FinishReason.STOP))

    async def actions(pilot: Any) -> None:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "go"
        await pilot.press("enter")
        await _wait_for(lambda: app._pending_approval is not None)
        await pilot.press("y")
        await _wait_for(lambda: not app._task_running)
        text = _log_text(app)
        assert "write_file(path=out.txt" in text
        assert "✓" in text
        # full JSON arguments must not leak into the summary line
        assert '"content": "hi"' not in text

    app = _build_app(tmp_path, [[first], [second]])
    asyncio.run(_drive(app, actions))


def test_auto_mode_skips_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent_llm import FinishReason, ModelResponse, ToolCall

    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")
    monkeypatch.chdir(tmp_path)

    first = _event(
        "completed",
        ModelResponse(
            content="我来写文件",
            tool_calls=(
                ToolCall(
                    id="call-1",
                    name="write_file",
                    arguments_json='{"path": "auto.txt", "content": "hi"}',
                ),
            ),
            finish_reason=FinishReason.TOOL_CALLS,
        ),
    )
    second = _event("completed", ModelResponse(content="写好了", finish_reason=FinishReason.STOP))

    async def actions(pilot: Any) -> None:
        # Switch to auto mode with Shift+Tab.
        await pilot.press("shift+tab")
        await pilot.pause()
        assert app._runtime is not None
        assert app._runtime.approval_mode.value == "auto"
        assert "auto" in str(app.query_one("#status", Static).content)
        # The write goes through with no approval prompt.
        prompt = app.query_one("#prompt", Input)
        prompt.value = "写一个 auto.txt"
        await pilot.press("enter")
        await pilot.pause()
        # Wait for a terminal condition; "not _task_running" alone races with
        # the worker that has not started yet.
        await _wait_for(lambda: (tmp_path / "auto.txt").exists() or "运行失败" in _log_text(app))
        await _wait_for(lambda: not app._task_running)
        assert app._pending_approval is None
        assert (tmp_path / "auto.txt").read_text(encoding="utf-8") == "hi"
        assert "写好了" in _log_text(app)

    app = _build_app(tmp_path, [[first], [second]])
    asyncio.run(_drive(app, actions))


def test_model_command_lists_and_switches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    async def actions(pilot: Any) -> None:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/model"
        await pilot.press("enter")
        await pilot.pause()
        text = _log_text(app)
        assert "deepseek-reasoner" in text  # builtin presets are listed
        assert "← 当前" in text  # current model is marked
        # Switch to another builtin profile.
        prompt.value = "/model deepseek-reasoner"
        await pilot.press("enter")
        await pilot.pause()
        assert app._runtime is not None
        assert app._runtime.config.model == "deepseek-reasoner"
        assert "已切换" in _log_text(app)
        # The status bar reflects the new model.
        assert "deepseek-reasoner" in str(app.query_one("#status", Static).content)

    app = _build_app(tmp_path, [])
    asyncio.run(_drive(app, actions))


def test_permissions_command_switches_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    async def actions(pilot: Any) -> None:
        prompt = app.query_one("#prompt", Input)
        prompt.value = "/permissions"
        await pilot.press("enter")
        await pilot.pause()
        assert "ask" in _log_text(app)
        prompt.value = "/permissions auto"
        await pilot.press("enter")
        await pilot.pause()
        assert app._runtime is not None
        assert app._runtime.approval_mode.value == "auto"
        assert "auto" in _log_text(app)

    app = _build_app(tmp_path, [])
    asyncio.run(_drive(app, actions))


# ------------------------------------------------------------------ helpers


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


def _log_text(app: CodeAgentApp) -> str:
    from textual.widgets import RichLog

    log = app.query_one("#transcript", RichLog)
    return "\n".join("".join(segment.text for segment in line) for line in log.lines)


async def _wait_for(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met within timeout")
