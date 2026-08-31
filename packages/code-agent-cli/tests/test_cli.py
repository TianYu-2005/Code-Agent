import asyncio
import io
from pathlib import Path

import pytest

from code_agent.cli.approval import TerminalApprovalPort
from code_agent.cli.commands import parse_input
from code_agent.cli.interrupt import CancelState
from code_agent.cli.renderer import TerminalRenderer
from code_agent.config import ConfigError, load_config


def test_parse_input_chat_vs_commands() -> None:
    chat = parse_input("fix the bug in main.py")
    quit_cmd = parse_input("/quit")
    exit_cmd = parse_input("/exit")
    model_cmd = parse_input("/model")
    with_arg = parse_input("/model deepseek-reasoner")
    unknown = parse_input("/fly")

    assert chat.kind == "chat"
    assert quit_cmd.kind == "exit"
    assert exit_cmd.kind == "exit"
    assert model_cmd.kind == "command"
    assert model_cmd.command == "model"
    assert with_arg.argument == "deepseek-reasoner"
    assert unknown.kind == "command"
    assert unknown.command == "fly"


def test_config_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CODE_AGENT_API_KEY", raising=False)
    try:
        load_config()
        raise AssertionError("expected ConfigError")
    except ConfigError as error:
        assert "CODE_AGENT_API_KEY" in str(error)


def test_config_loads_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")
    monkeypatch.setenv("CODE_AGENT_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("CODE_AGENT_MODEL", "deepseek-v4-pro")

    config = load_config(workspace=str(tmp_path))

    assert config.model == "deepseek-v4-pro"
    assert config.base_url == "https://api.deepseek.com"
    assert config.provider_config.api_key.get_secret_value() == "test-key"
    assert config.provider_config.trusted_base_url_hosts == frozenset({"api.deepseek.com"})


def test_config_defaults_to_deepseek(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")
    monkeypatch.delenv("CODE_AGENT_BASE_URL", raising=False)
    monkeypatch.delenv("CODE_AGENT_MODEL", raising=False)

    config = load_config(workspace=str(tmp_path))

    assert config.model == "deepseek-v4-flash"
    assert config.base_url == "https://api.deepseek.com"
    assert config.base_url_host == "api.deepseek.com"
    assert config.provider_config.trusted_base_url_hosts == frozenset({"api.deepseek.com"})


def test_renderer_streams_delta_and_tool_status() -> None:
    output = io.StringIO()
    renderer = TerminalRenderer(output_stream=output)

    from code_agent_core import RuntimeEvent, RuntimeEventType

    delta = RuntimeEvent(
        type=RuntimeEventType.MODEL_DELTA,
        session_id="s1",
        run_id="r1",
        turn_id="t1",
        model_call_id="m1",
        payload={"text": "hello"},
    )
    tool_start = RuntimeEvent(
        type=RuntimeEventType.TOOL_STARTED,
        session_id="s1",
        run_id="r1",
        turn_id="t1",
        tool_call_id="tc1",
    )
    tool_done = RuntimeEvent(
        type=RuntimeEventType.TOOL_COMPLETED,
        session_id="s1",
        run_id="r1",
        turn_id="t1",
        tool_call_id="tc1",
        payload={"status": "success"},
    )

    renderer.on_event(delta)
    renderer.on_event(tool_start)
    renderer.on_event(tool_done)

    text = output.getvalue()
    assert "hello" in text
    assert "success" in text

    started = RuntimeEvent(
        type=RuntimeEventType.MODEL_STARTED,
        session_id="s1",
        run_id="r2",
        turn_id="t2",
        model_call_id="m2",
    )
    renderer.on_event(started)
    assert output.getvalue().endswith("\n")


def test_renderer_shows_context_compacted() -> None:
    output = io.StringIO()
    renderer = TerminalRenderer(output_stream=output)

    from code_agent_core import RuntimeEvent, RuntimeEventType

    compacted = RuntimeEvent(
        type=RuntimeEventType.CONTEXT_COMPACTED,
        session_id="s1",
        run_id="r1",
        turn_id="t1",
        payload={"status": "compacted", "messages_compacted": 12, "tokens_before": 41000},
    )
    failed = RuntimeEvent(
        type=RuntimeEventType.CONTEXT_COMPACTED,
        session_id="s1",
        run_id="r2",
        turn_id="t2",
        payload={"status": "failed", "messages_compacted": 0, "tokens_before": 41000},
    )

    renderer.on_event(compacted)
    renderer.on_event(failed)

    text = output.getvalue()
    assert "上下文已自动压缩" in text
    assert "12" in text
    assert "压缩失败" in text


def test_approval_port_reads_yes() -> None:
    output = io.StringIO()
    input_stream = io.StringIO("y\n")

    from code_agent_core import (
        ApprovalRequest,
        ValidatedToolCall,
    )
    from code_agent_core.tools.base import ToolEffect, ToolTarget

    call = ValidatedToolCall(
        id="c1",
        name="write_file",
        arguments={"path": "a.txt"},
        targets=(ToolTarget(effect=ToolEffect.WRITE, resource="a.txt"),),
        effective_effects=frozenset({ToolEffect.WRITE}),
        fingerprint="a" * 64,
    )
    request = ApprovalRequest(call=call, description="write a file")
    port = TerminalApprovalPort(input_stream=input_stream, output_stream=output)

    response = asyncio.run(
        port.request(
            request,
            None,  # type: ignore[arg-type]
        )
    )

    assert response.approved is True
    assert "write_file" in output.getvalue()


def test_cancel_state_tracks_signal() -> None:
    state = CancelState()

    assert state.is_cancelled is False
    state.cancel()
    assert state.is_cancelled is True
    state.reset()
    assert state.is_cancelled is False


def test_banner_and_info_use_streams() -> None:
    output = io.StringIO()
    renderer = TerminalRenderer(output_stream=output)

    renderer.banner("/workspace", "deepseek-v4-flash")
    renderer.info("hello info")

    text = output.getvalue()
    assert "Code Agent" in text
    assert "deepseek-v4-flash" in text
    assert "hello info" in text


def test_runtime_streams_events_to_renderer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The composition root must pipe loop events into the renderer."""
    from code_agent.bootstrap import AgentRuntime
    from code_agent_llm import (
        FakeProvider,
        FinishReason,
        Message,
        MessageRole,
        ModelEvent,
        ModelEventType,
        ModelResponse,
        ToolCall,
    )

    monkeypatch.chdir(tmp_path)
    (tmp_path / "note.txt").write_text("hello agent\n", encoding="utf-8")
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")

    first_turn = (
        ModelEvent(type=ModelEventType.TEXT_DELTA, text_delta="Reading the file.\n"),
        ModelEvent(
            type=ModelEventType.COMPLETED,
            response=ModelResponse(
                content="Reading the file.",
                tool_calls=(
                    ToolCall(id="call-1", name="read_file", arguments_json='{"path": "note.txt"}'),
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
        ),
    )
    second_turn = (
        ModelEvent(type=ModelEventType.TEXT_DELTA, text_delta="All done."),
        ModelEvent(
            type=ModelEventType.COMPLETED,
            response=ModelResponse(
                content="All done.",
                tool_calls=(),
                finish_reason=FinishReason.STOP,
            ),
        ),
    )
    provider = FakeProvider((first_turn, second_turn))
    output = io.StringIO()
    config = load_config(workspace=str(tmp_path))
    runtime = AgentRuntime(
        config,
        provider=provider,
        renderer=TerminalRenderer(output_stream=output),
    )

    message = Message(id="user-1", role=MessageRole.USER, content="read the note")
    result = asyncio.run(runtime.make_loop().run(message))

    text = output.getvalue()
    assert result.end_reason.value == "completed"
    assert "Reading the file." in text
    assert "All done." in text
    assert "[success]" in text
