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


def test_banner_and_speaker_labels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from code_agent_llm import FinishReason, ModelResponse

    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    async def actions(pilot: Any) -> None:
        assert "███████" in _log_text(app)  # ASCII-art banner present
        prompt = app.query_one("#prompt", Input)
        prompt.value = "ping"
        await pilot.press("enter")
        await _wait_for(lambda: not app._task_running and "pong" in _log_text(app))
        log_text = _log_text(app)
        assert "User ping" in log_text
        assert "Agent pong" in log_text

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
            await pilot.pause()
            # The inline height is derived from the Screen CSS (80vh),
            # so it tracks the terminal height instead of staying fixed.
            return app._get_inline_height()

    heights.append(asyncio.run(measure((100, 40))))
    heights.append(asyncio.run(measure((100, 20))))
    assert heights[0] >= 30
    assert heights[1] <= 18
    assert heights[0] > heights[1]


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
