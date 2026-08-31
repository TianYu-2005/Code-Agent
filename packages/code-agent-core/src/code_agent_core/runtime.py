"""Contracts describing an agent run and its runtime capabilities."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import Field, JsonValue, field_validator

from code_agent_llm import ProtocolModel

from .events import RuntimeEvent


class CancellationToken(Protocol):
    """Cooperative cancellation capability passed to runtime components."""

    @property
    def is_cancelled(self) -> bool:
        """Return whether cancellation has been requested."""
        ...

    async def wait(self) -> None:
        """Wait until cancellation is requested."""
        ...


class EventSink(Protocol):
    """Destination for observable runtime events."""

    async def emit(self, event: RuntimeEvent) -> None:
        """Publish one runtime event."""
        ...


class RunBudgets(ProtocolModel):
    """Limits applied to one agent run."""

    max_turns: int = Field(default=20, ge=1)
    timeout_seconds: float = Field(default=900.0, gt=0)
    max_input_tokens: int | None = Field(default=None, ge=1)
    max_output_tokens: int | None = Field(default=None, ge=1)


class RunSpec(ProtocolModel):
    """Immutable description of one root or child agent run."""

    session_id: str = Field(min_length=1)
    parent_run_id: str | None = Field(default=None, min_length=1)
    agent_name: str = Field(default="default", min_length=1)
    model: str = Field(min_length=1)
    tool_set: frozenset[str] = frozenset()
    budgets: RunBudgets = Field(default_factory=RunBudgets)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("tool_set")
    @classmethod
    def validate_tool_names(cls, value: frozenset[str]) -> frozenset[str]:
        """Require each selected tool to use the public tool-name grammar."""
        import re

        pattern = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
        if any(pattern.fullmatch(name) is None for name in value):
            raise ValueError("tool_set contains an invalid tool name")
        return value


@dataclass(frozen=True, slots=True, kw_only=True)
class PermissionContext:
    """Session-scoped grants available during tool authorization."""

    granted_patterns: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionContext:
    """Non-serializable runtime capabilities available to a tool."""

    workspace: Path
    session_id: str
    run_id: str
    cancellation: CancellationToken
    permission_context: PermissionContext
    event_sink: EventSink

    def __post_init__(self) -> None:
        """Reject ambiguous identifiers and non-canonical workspace paths."""
        if not self.workspace.is_absolute():
            raise ValueError("workspace must be an absolute path")
        if self.workspace != self.workspace.resolve():
            raise ValueError("workspace must be canonical")
        if not self.session_id:
            raise ValueError("session_id cannot be empty")
        if not self.run_id:
            raise ValueError("run_id cannot be empty")
