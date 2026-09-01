import asyncio
from typing import Any

from code_agent_core import (
    ApprovalResponse,
    DefaultPermissionPolicy,
    RunBudgets,
    RunSpec,
    RuntimeEvent,
    ToolEffect,
    ToolExecutor,
    ToolOrigin,
    ToolOutcome,
    ToolRegistry,
    ToolSpec,
)
from code_agent_core.context import ContextManager
from code_agent_core.runtime.loop import AgentLoop, LoopEndReason
from code_agent_core.runtime.spec import ExecutionContext
from code_agent_core.session import SessionStore
from code_agent_core.tools.permissions import ApprovalRequest
from code_agent_llm import (
    FakeProvider,
    FinishReason,
    Message,
    MessageRole,
    ModelEvent,
    ModelEventType,
    ModelProviderError,
    ModelResponse,
    ProviderErrorCode,
    ToolCall,
)


class EchoTool:
    """Read-only tool that echoes its arguments."""

    def __init__(self) -> None:
        self.executions = 0
        self.spec = ToolSpec(
            name="echo",
            description="Echo the value",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            effects=frozenset({ToolEffect.READ}),
            origin=ToolOrigin.BUILTIN,
        )

    def resolve_targets(
        self,
        call: Any,
        context: ExecutionContext,
    ) -> tuple[Any, ...]:
        return ()

    async def execute(
        self,
        call: Any,
        context: ExecutionContext,
        output: Any,
    ) -> ToolOutcome:
        self.executions += 1
        output.write("echoed")
        return ToolOutcome()

    async def abort(
        self,
        call: Any,
        context: ExecutionContext,
        reason: Any,
    ) -> None:
        return None


class RecordingSink:
    """Event sink that stores every emitted event."""

    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    async def emit(self, event: RuntimeEvent) -> None:
        self.events.append(event)


class AlwaysApprove:
    """Approval port that approves every request."""

    async def request(
        self,
        request: ApprovalRequest,
        context: ExecutionContext,
    ) -> ApprovalResponse:
        return ApprovalResponse(
            fingerprint=request.call.fingerprint,
            approved=True,
        )


def make_loop(
    provider_scripts: list[Any],
    *,
    session: SessionStore | None = None,
    max_turns: int = 10,
    sink: RecordingSink | None = None,
) -> tuple[AgentLoop, EchoTool]:
    provider = FakeProvider(provider_scripts)
    session = session or SessionStore()
    registry = ToolRegistry()
    echo = EchoTool()
    registry.register(echo, origin=ToolOrigin.BUILTIN)
    executor = ToolExecutor(registry, DefaultPermissionPolicy(), AlwaysApprove())
    loop = AgentLoop(
        provider=provider,
        session=session,
        context_manager=ContextManager(session),
        tool_registry=registry,
        tool_executor=executor,
        run_spec=RunSpec(
            session_id="session-1",
            model="test-model",
            budgets=RunBudgets(max_turns=max_turns),
        ),
        event_sink=sink,
    )
    return loop, echo


def final_answer(text: str = "done") -> list[ModelEvent]:
    return [
        ModelEvent(
            type=ModelEventType.COMPLETED,
            response=ModelResponse(content=text, finish_reason=FinishReason.STOP),
        )
    ]


def tool_call_response(call_id: str = "call-1", name: str = "echo") -> list[ModelEvent]:
    return [
        ModelEvent(
            type=ModelEventType.COMPLETED,
            response=ModelResponse(
                tool_calls=(ToolCall(id=call_id, name=name, arguments_json='{"value":"hi"}'),),
                finish_reason=FinishReason.TOOL_CALLS,
            ),
        )
    ]


def test_loop_completes_after_final_answer() -> None:
    loop, _ = make_loop([final_answer("all done")])

    result = asyncio.run(loop.run(Message(id="m1", role=MessageRole.USER, content="task")))

    assert result.end_reason is LoopEndReason.COMPLETED
    assert result.final_message is not None
    assert result.final_message.content == "all done"
    assert result.turns == 1


def test_loop_executes_tool_then_finishes() -> None:
    loop, echo = make_loop([tool_call_response(), final_answer("finished")])

    result = asyncio.run(loop.run(Message(id="m1", role=MessageRole.USER, content="echo hi")))

    assert result.end_reason is LoopEndReason.COMPLETED
    assert echo is not None and echo.executions == 1
    assert result.turns == 2


def test_loop_records_conversation_in_session() -> None:
    session = SessionStore()
    loop, _ = make_loop([tool_call_response(), final_answer()], session=session)

    asyncio.run(loop.run(Message(id="m1", role=MessageRole.USER, content="task")))

    contents = session.messages()
    roles = [payload.message.role for payload in contents]
    assert roles == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.TOOL,
        MessageRole.ASSISTANT,
    ]
    tool_message = contents[2].message
    assert tool_message.tool_call_id == "call-1"
    assert tool_message.content == "echoed"


def test_loop_stops_at_max_turns() -> None:
    scripts = [tool_call_response(f"call-{i}") for i in range(5)]
    loop, _ = make_loop(scripts, max_turns=3)

    result = asyncio.run(loop.run(Message(id="m1", role=MessageRole.USER, content="task")))

    assert result.end_reason is LoopEndReason.MAX_TURNS
    assert result.turns == 3


def test_loop_stops_after_three_equivalent_tool_failures() -> None:
    scripts = [
        [
            ModelEvent(
                type=ModelEventType.COMPLETED,
                response=ModelResponse(
                    tool_calls=(
                        ToolCall(
                            id=f"bad-{index}",
                            name="echo",
                            arguments_json='{"wrong":"value"}',
                        ),
                    ),
                    finish_reason=FinishReason.TOOL_CALLS,
                ),
            )
        ]
        for index in range(3)
    ]
    loop, echo = make_loop(scripts, max_turns=10)

    result = asyncio.run(loop.run(Message(id="m1", role=MessageRole.USER, content="task")))

    assert result.end_reason is LoopEndReason.ERROR
    assert result.error is not None
    assert "failed three consecutive times" in result.error
    assert echo.executions == 0


def test_loop_reports_provider_error() -> None:
    error = ModelProviderError(
        ProviderErrorCode.AUTHENTICATION,
        "invalid api key",
    )
    loop, _ = make_loop([error])

    result = asyncio.run(loop.run(Message(id="m1", role=MessageRole.USER, content="task")))

    assert result.end_reason is LoopEndReason.ERROR
    assert result.error is not None


def test_loop_emits_lifecycle_events() -> None:
    sink = RecordingSink()
    loop, _ = make_loop([tool_call_response(), final_answer()], sink=sink)

    asyncio.run(loop.run(Message(id="m1", role=MessageRole.USER, content="task")))

    types = [event.type.value for event in sink.events]
    assert "run_started" in types
    assert "model_started" in types
    assert "tool_started" in types
    assert "tool_completed" in types
    assert "model_completed" in types
    assert "run_completed" in types
