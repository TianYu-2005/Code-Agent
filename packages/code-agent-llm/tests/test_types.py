import pytest
from pydantic import ValidationError

from code_agent_llm import (
    FinishReason,
    GenerationConfig,
    Message,
    MessageRole,
    ModelEvent,
    ModelEventType,
    ModelRequest,
    ModelResponse,
    ModelToolSpec,
    TokenUsage,
    ToolCall,
    ToolCallDelta,
)


def make_tool_call(call_id: str = "call-1") -> ToolCall:
    return ToolCall(
        id=call_id,
        name="read_file",
        arguments_json='{"path":"README.md","options":{"line":1}}',
    )


def test_message_accepts_valid_tool_exchange() -> None:
    tool_call = make_tool_call()
    assistant = Message(
        id="message-1",
        role=MessageRole.ASSISTANT,
        tool_calls=(tool_call,),
    )
    result = Message(
        id="message-2",
        role=MessageRole.TOOL,
        content="file contents",
        tool_call_id=tool_call.id,
    )

    assert assistant.tool_calls == (tool_call,)
    assert result.tool_call_id == tool_call.id


@pytest.mark.parametrize("role", [MessageRole.SYSTEM, MessageRole.USER])
def test_non_assistant_message_rejects_tool_calls(role: MessageRole) -> None:
    with pytest.raises(ValidationError, match="cannot contain tool fields"):
        Message(id="message-1", role=role, tool_calls=(make_tool_call(),))


def test_tool_message_requires_non_empty_tool_call_id() -> None:
    with pytest.raises(ValidationError):
        Message(id="message-1", role=MessageRole.TOOL, tool_call_id="")


def test_assistant_message_rejects_duplicate_tool_call_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate tool call ids"):
        Message(
            id="message-1",
            role=MessageRole.ASSISTANT,
            tool_calls=(make_tool_call(), make_tool_call()),
        )


def test_protocol_models_are_strict_and_forbid_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Message.model_validate({"id": "message-1", "role": "user"})
    with pytest.raises(ValidationError, match="Extra inputs"):
        ToolCall.model_validate(
            {
                "id": "call-1",
                "name": "read_file",
                "arguments_json": "{}",
                "unknown": True,
            }
        )


def test_protocol_models_are_frozen() -> None:
    tool_call = make_tool_call()

    with pytest.raises(ValidationError, match="frozen"):
        tool_call.name = "write_file"

    restored = ToolCall.model_validate_json(tool_call.model_dump_json())
    assert restored == tool_call


def test_protocol_models_reject_non_finite_generation_numbers() -> None:
    for number in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValidationError):
            GenerationConfig(temperature=number)


def test_model_request_validates_tools_and_generation_config() -> None:
    tool = ModelToolSpec(
        name="read_file",
        description="Read one file",
        input_schema={"type": "object"},
    )
    message = Message(id="message-1", role=MessageRole.USER, content="read")

    with pytest.raises(ValidationError, match="duplicate tool names"):
        ModelRequest(messages=(message,), tools=(tool, tool), model="test-model")
    with pytest.raises(ValidationError, match="stop sequences"):
        GenerationConfig(stop=("done", "done"))


def test_token_usage_rejects_inconsistent_total() -> None:
    with pytest.raises(ValidationError, match="total_tokens"):
        TokenUsage(input_tokens=2, output_tokens=3, total_tokens=4)


def test_model_response_validates_finish_reason_and_tool_calls() -> None:
    tool_call = make_tool_call()

    with pytest.raises(ValidationError, match="must match"):
        ModelResponse(finish_reason=FinishReason.TOOL_CALLS)
    with pytest.raises(ValidationError, match="must match"):
        ModelResponse(tool_calls=(tool_call,), finish_reason=FinishReason.STOP)
    with pytest.raises(ValidationError, match="duplicate tool call ids"):
        ModelResponse(
            tool_calls=(tool_call, tool_call),
            finish_reason=FinishReason.TOOL_CALLS,
        )


def test_missing_usage_is_distinct_from_zero_usage() -> None:
    unknown = ModelResponse(finish_reason=FinishReason.STOP)
    known = ModelResponse(
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0),
    )

    assert unknown.usage is None
    assert known.usage is not None


def test_model_event_requires_matching_payload() -> None:
    with pytest.raises(ValidationError, match="matching payload"):
        ModelEvent(type=ModelEventType.USAGE)
    with pytest.raises(ValidationError, match="different event type"):
        ModelEvent(
            type=ModelEventType.TOOL_CALL_DELTA,
            tool_call_delta=ToolCallDelta(index=0, name="read_file"),
            text_delta="unexpected",
        )


def test_all_model_event_variants_round_trip() -> None:
    usage = TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3)
    events = (
        ModelEvent(type=ModelEventType.TEXT_DELTA, text_delta="hello"),
        ModelEvent(
            type=ModelEventType.TOOL_CALL_DELTA,
            tool_call_delta=ToolCallDelta(index=0, id="call-1", name="read_file"),
        ),
        ModelEvent(type=ModelEventType.USAGE, usage=usage),
        ModelEvent(
            type=ModelEventType.COMPLETED,
            response=ModelResponse(finish_reason=FinishReason.STOP, usage=usage),
        ),
    )

    for event in events:
        assert ModelEvent.model_validate_json(event.model_dump_json()) == event


def test_model_request_json_round_trip() -> None:
    request = ModelRequest(
        messages=(Message(id="message-1", role=MessageRole.USER, content="hello"),),
        model="test-model",
        parameters=GenerationConfig(
            temperature=0.0,
            provider_options={"vendor": {"reasoning": "medium"}},
        ),
    )

    restored = ModelRequest.model_validate_json(request.model_dump_json())

    assert restored == request
