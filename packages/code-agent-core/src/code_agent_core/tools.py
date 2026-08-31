"""Tool contracts shared by the agent runtime and tool implementations."""

import json
from enum import StrEnum
from typing import Self

from pydantic import Field, JsonValue, model_validator

from code_agent_llm import ProtocolModel

MAX_TOOL_RESULT_CHARS = 32_768
MAX_TOOL_RESULT_BYTES = 65_536


class ToolEffect(StrEnum):
    """Observable effects a tool may have."""

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


class ToolSpec(ProtocolModel):
    """Description and execution policy metadata for one tool."""

    name: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    description: str = Field(min_length=1)
    input_schema: dict[str, JsonValue]
    effects: frozenset[ToolEffect] = frozenset()
    timeout_seconds: float = Field(default=30.0, gt=0)
    concurrency_key: str | None = Field(default=None, min_length=1)
    origin: ToolOrigin = ToolOrigin.BUILTIN


class ToolResult(ProtocolModel):
    """Size-bounded result returned across the tool execution boundary."""

    status: ToolStatus
    content: str = Field(default="", max_length=MAX_TOOL_RESULT_CHARS)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_total_size(self) -> Self:
        """Prevent metadata from bypassing the tool result output limit."""
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
        """Whether execution completed successfully."""
        return self.status is ToolStatus.SUCCESS
