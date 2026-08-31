"""Provider-independent model protocol types."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from .immutable import freeze_json


class ProtocolModel(BaseModel):
    """Strict, deeply immutable base for serializable protocol values."""

    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )

    @model_validator(mode="after")
    def freeze_json_containers(self) -> Self:
        """Defensively copy and freeze nested JSON containers."""
        for field_name in type(self).model_fields:
            value = getattr(self, field_name)
            frozen_value = freeze_json(value)
            if frozen_value is not value:
                object.__setattr__(self, field_name, frozen_value)
        return self


class MessageRole(StrEnum):
    """Roles supported by the internal message protocol."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class FinishReason(StrEnum):
    """Provider-independent model completion reasons."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    LENGTH = "length"
    CONTENT_FILTER = "content_filter"
    CANCELLED = "cancelled"
    ERROR = "error"
    UNKNOWN = "unknown"


class ModelEventType(StrEnum):
    """Kinds of events emitted by a streaming model call."""

    TEXT_DELTA = "text_delta"
    TOOL_CALL_DELTA = "tool_call_delta"
    USAGE = "usage"
    COMPLETED = "completed"


class ToolCall(ProtocolModel):
    """An untrusted tool invocation normalized by a model adapter."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    arguments_json: str = "{}"


class ModelToolSpec(ProtocolModel):
    """Tool definition exposed to a model provider."""

    name: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue]


class Message(ProtocolModel):
    """A message exchanged between the runtime and a model provider."""

    id: str = Field(min_length=1)
    role: MessageRole
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = Field(default=None, min_length=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_role_fields(self) -> Self:
        """Ensure tool-related fields are valid for the selected role."""
        if self.role is MessageRole.ASSISTANT:
            if self.tool_call_id is not None:
                raise ValueError("assistant messages cannot reference a tool call")
            tool_call_ids = [tool_call.id for tool_call in self.tool_calls]
            if len(tool_call_ids) != len(set(tool_call_ids)):
                raise ValueError("assistant message contains duplicate tool call ids")
        elif self.role is MessageRole.TOOL:
            if not self.tool_call_id:
                raise ValueError("tool messages require tool_call_id")
            if self.tool_calls:
                raise ValueError("tool messages cannot request tools")
        elif self.tool_calls or self.tool_call_id is not None:
            raise ValueError(f"{self.role.value} messages cannot contain tool fields")
        return self


class TokenUsage(ProtocolModel):
    """Normalized token accounting for one model call."""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        """Reject totals smaller than the known input and output counts."""
        if self.total_tokens < self.input_tokens + self.output_tokens:
            raise ValueError("total_tokens cannot be smaller than input_tokens + output_tokens")
        return self


class GenerationConfig(ProtocolModel):
    """Provider-neutral generation parameters plus namespaced adapter options."""

    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    max_output_tokens: int | None = Field(default=None, ge=1)
    stop: tuple[str, ...] = ()
    seed: int | None = None
    provider_options: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("stop")
    @classmethod
    def validate_stop_sequences(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject empty or duplicate stop sequences."""
        if any(not sequence for sequence in value):
            raise ValueError("stop sequences cannot be empty")
        if len(value) != len(set(value)):
            raise ValueError("stop sequences must be unique")
        return value


class ModelRequest(ProtocolModel):
    """A provider-independent model invocation request."""

    messages: tuple[Message, ...] = Field(min_length=1)
    tools: tuple[ModelToolSpec, ...] = ()
    model: str = Field(min_length=1)
    parameters: GenerationConfig = Field(default_factory=GenerationConfig)

    @model_validator(mode="after")
    def validate_unique_tools(self) -> Self:
        """Tool names in one request must be unique."""
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("model request contains duplicate tool names")
        return self


class ModelResponse(ProtocolModel):
    """A complete normalized response from a model provider."""

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    finish_reason: FinishReason
    usage: TokenUsage | None = None

    @model_validator(mode="after")
    def validate_tool_calls(self) -> Self:
        """Require unique calls and a finish reason consistent with them."""
        ids = [tool_call.id for tool_call in self.tool_calls]
        if len(ids) != len(set(ids)):
            raise ValueError("model response contains duplicate tool call ids")
        has_tool_calls = bool(self.tool_calls)
        if (self.finish_reason is FinishReason.TOOL_CALLS) != has_tool_calls:
            raise ValueError("tool_calls finish reason must match the presence of tool calls")
        return self


class ToolCallDelta(ProtocolModel):
    """Incremental data for a streamed tool call."""

    index: int = Field(ge=0)
    id: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1)
    arguments_delta: str = ""


class ModelEvent(ProtocolModel):
    """A single event in a provider-independent model stream."""

    type: ModelEventType
    text_delta: str | None = None
    tool_call_delta: ToolCallDelta | None = None
    usage: TokenUsage | None = None
    response: ModelResponse | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        """Require exactly the payload associated with the event type."""
        values = {
            ModelEventType.TEXT_DELTA: self.text_delta,
            ModelEventType.TOOL_CALL_DELTA: self.tool_call_delta,
            ModelEventType.USAGE: self.usage,
            ModelEventType.COMPLETED: self.response,
        }
        if values[self.type] is None:
            raise ValueError(f"{self.type.value} event requires its matching payload")
        if any(value is not None for kind, value in values.items() if kind is not self.type):
            raise ValueError("model event contains a payload for a different event type")
        return self
