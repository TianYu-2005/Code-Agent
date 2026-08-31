"""Stable contracts for tools and their execution results."""

import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol, Self, runtime_checkable

from pydantic import Field, JsonValue, model_validator

from code_agent_llm import ProtocolModel

from ..runtime.spec import ExecutionContext

MAX_TOOL_ARGUMENT_BYTES = 65_536
MAX_TOOL_RESULT_CHARS = 32_768
MAX_TOOL_RESULT_BYTES = 65_536


class ToolEffect(StrEnum):
    """Observable effects a tool may have."""

    UNKNOWN = "unknown"
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"


class ToolOrigin(StrEnum):
    """Source that contributed a tool to the registry."""

    BUILTIN = "builtin"
    EXTENSION = "extension"
    MCP = "mcp"
    PLUGIN = "plugin"


class ToolStatus(StrEnum):
    """Terminal status of a tool invocation."""

    SUCCESS = "success"
    ERROR = "error"
    DENIED = "denied"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ToolAbortReason(StrEnum):
    """Why ToolExecutor requested resource cleanup."""

    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    CALLER_CANCELLED = "caller_cancelled"


class ToolSpec(ProtocolModel):
    """Description and conservative effect declaration for one tool."""

    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    description: str = Field(min_length=1, max_length=4_096)
    input_schema: dict[str, JsonValue]
    effects: frozenset[ToolEffect] = frozenset({ToolEffect.UNKNOWN})
    timeout_seconds: float = Field(default=30.0, gt=0, le=3_600)
    concurrency_key: str | None = Field(default=None, min_length=1, max_length=256)
    origin: ToolOrigin = ToolOrigin.BUILTIN


class ToolTarget(ProtocolModel):
    """Concrete resource and effect resolved from validated arguments."""

    effect: ToolEffect
    resource: str = Field(min_length=1, max_length=4_096)
    external: bool = False
    sensitive: bool = False


class ValidatedToolCall(ProtocolModel):
    """Arguments validated by ToolExecutor against a registered schema."""

    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    arguments: dict[str, JsonValue]
    targets: tuple[ToolTarget, ...] = Field(max_length=64)
    effective_effects: frozenset[ToolEffect]
    fingerprint: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class ToolOutcome(ProtocolModel):
    """Small structured outcome returned by a tool implementation."""

    metadata: dict[str, JsonValue] = Field(default_factory=dict)


@runtime_checkable
class ToolOutputSink(Protocol):
    """Bounded incremental text sink supplied by ToolExecutor."""

    def write(self, text: str) -> None:
        """Append text while respecting the configured output limit."""
        ...


@runtime_checkable
class Tool(Protocol):
    """Executable capability; authorization remains ToolExecutor's responsibility."""

    @property
    def spec(self) -> ToolSpec:
        """Return the immutable public specification."""
        ...

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        """Resolve invocation-specific resources before authorization."""
        ...

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: ToolOutputSink,
    ) -> ToolOutcome:
        """Execute one already validated and authorized invocation."""
        ...

    async def abort(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        reason: ToolAbortReason,
    ) -> None:
        """Release external resources after timeout or cancellation."""
        ...


class ToolResult(ProtocolModel):
    """Size-bounded result returned across the execution boundary."""

    status: ToolStatus
    content: str = Field(default="", max_length=MAX_TOOL_RESULT_CHARS)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_total_size(self) -> Self:
        encoded = json.dumps(
            {
                "status": self.status.value,
                "content": self.content,
                "metadata": self.metadata,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
        if len(encoded) > MAX_TOOL_RESULT_BYTES:
            raise ValueError(f"tool result cannot exceed {MAX_TOOL_RESULT_BYTES} bytes")
        return self

    @property
    def is_success(self) -> bool:
        return self.status is ToolStatus.SUCCESS
