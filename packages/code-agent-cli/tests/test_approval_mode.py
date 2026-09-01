"""Tests for the runtime-switchable approval mode and model switching."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from code_agent.bootstrap import AgentRuntime
from code_agent.cli.approval import ModeApprovalPort
from code_agent.config import ApprovalMode, load_config
from code_agent_core import (
    ApprovalRequest,
    ApprovalResponse,
    ExecutionContext,
    PermissionContext,
    ToolEffect,
    ToolTarget,
    ValidatedToolCall,
)
from code_agent_llm import FakeProvider


@dataclass
class _Token:
    event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def is_cancelled(self) -> bool:
        return self.event.is_set()

    async def wait(self) -> None:
        await self.event.wait()


class _Sink:
    async def emit(self, event: object) -> None:
        return None


def _context(tmp_path: Path) -> ExecutionContext:
    return ExecutionContext(
        workspace=tmp_path,
        session_id="session-1",
        run_id="run-1",
        cancellation=_Token(),
        permission_context=PermissionContext(),
        event_sink=_Sink(),
    )


def _request() -> ApprovalRequest:
    call = ValidatedToolCall(
        id="call-1",
        name="write_file",
        arguments={"path": "a.txt"},
        targets=(ToolTarget(resource="a.txt", effect=ToolEffect.WRITE),),
        effective_effects=frozenset({ToolEffect.WRITE}),
        fingerprint="a" * 64,
    )
    return ApprovalRequest(call=call, description="write a file")


class _RecordingPort:
    """Approval port that records calls and denies everything."""

    def __init__(self) -> None:
        self.calls: list[ApprovalRequest] = []

    async def request(
        self,
        request: ApprovalRequest,
        context: ExecutionContext,
    ) -> ApprovalResponse:
        self.calls.append(request)
        return ApprovalResponse(fingerprint=request.call.fingerprint, approved=False)


def test_auto_mode_approves_without_asking(tmp_path: Path) -> None:
    inner = _RecordingPort()
    port = ModeApprovalPort(inner, mode=ApprovalMode.AUTO)

    response = asyncio.run(port.request(_request(), _context(tmp_path)))

    assert response.approved is True
    assert response.fingerprint == "a" * 64
    assert inner.calls == []  # never reached the interactive port


def test_ask_mode_delegates_to_inner_port(tmp_path: Path) -> None:
    inner = _RecordingPort()
    port = ModeApprovalPort(inner, mode=ApprovalMode.ASK)

    response = asyncio.run(port.request(_request(), _context(tmp_path)))

    assert response.approved is False  # inner port answered
    assert len(inner.calls) == 1


def test_mode_switches_at_runtime(tmp_path: Path) -> None:
    inner = _RecordingPort()
    port = ModeApprovalPort(inner)

    port.set_mode(ApprovalMode.AUTO)
    assert port.mode is ApprovalMode.AUTO
    first = asyncio.run(port.request(_request(), _context(tmp_path)))
    assert first.approved is True
    assert inner.calls == []

    port.set_mode(ApprovalMode.ASK)
    second = asyncio.run(port.request(_request(), _context(tmp_path)))
    assert second.approved is False
    assert len(inner.calls) == 1


# ---------------------------------------------------------------- model switch


def _runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[AgentRuntime, FakeProvider]:
    monkeypatch.setenv("CODE_AGENT_API_KEY", "test-key")
    monkeypatch.setenv("CODE_AGENT_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(exist_ok=True)
    config = load_config(workspace=str(tmp_path))
    provider = FakeProvider([])
    runtime = AgentRuntime(config, provider=provider)
    return runtime, provider


def test_switch_to_builtin_profile_updates_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, provider = _runtime(tmp_path, monkeypatch)

    runtime.switch_model("deepseek-reasoner")

    assert runtime.config.model == "deepseek-reasoner"
    assert runtime.run_spec.model == "deepseek-reasoner"
    # same endpoint: the injected provider stays in place
    assert runtime.provider is provider


def test_switch_to_bare_model_name_keeps_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, provider = _runtime(tmp_path, monkeypatch)

    runtime.switch_model("some-custom-model")

    assert runtime.config.model == "some-custom-model"
    assert runtime.config.base_url == "https://api.deepseek.com"
    assert runtime.provider is provider


def test_switch_to_profile_with_new_endpoint_rebuilds_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from code_agent.config import ModelProfile

    runtime, _ = _runtime(tmp_path, monkeypatch)
    runtime.config.profiles["local"] = ModelProfile(
        name="local",
        model="qwen",
        base_url="http://localhost:11434/v1",
        api_key="ollama",
    )

    runtime.switch_model("local")

    assert runtime.config.model == "qwen"
    assert runtime.config.base_url == "http://localhost:11434/v1"
    assert runtime.run_spec.model == "qwen"


def test_approval_mode_roundtrip_on_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, _ = _runtime(tmp_path, monkeypatch)

    assert runtime.approval_mode.value == "ask"
    runtime.set_approval_mode(ApprovalMode.AUTO)
    assert runtime.approval_mode is ApprovalMode.AUTO
    assert runtime.config.approval_mode is ApprovalMode.AUTO
