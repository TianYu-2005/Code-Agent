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
    monkeypatch.setenv("CODE_AGENT_MODEL", "deepseek-chat")

    config = load_config(workspace=str(tmp_path))

    assert config.model == "deepseek-chat"
    assert config.base_url == "https://api.deepseek.com"
    assert config.provider_config.api_key.get_secret_value() == "test-key"
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

    renderer.banner("/workspace", "deepseek-chat")
    renderer.info("hello info")

    text = output.getvalue()
    assert "Code Agent" in text
    assert "deepseek-chat" in text
    assert "hello info" in text
