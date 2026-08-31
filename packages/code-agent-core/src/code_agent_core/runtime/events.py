"""Versioned events emitted by the agent runtime."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from code_agent_llm import ProtocolModel


class RuntimeEventType(StrEnum):
    """Stable lifecycle event names exposed by the runtime."""

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    TURN_STARTED = "turn_started"
    TURN_COMPLETED = "turn_completed"
    MODEL_STARTED = "model_started"
    MODEL_DELTA = "model_delta"
    MODEL_COMPLETED = "model_completed"
    TOOL_REQUESTED = "tool_requested"
    PERMISSION_REQUESTED = "permission_requested"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    CONTEXT_COMPACTED = "context_compacted"
    STEERING_RECEIVED = "steering_received"
    FOLLOW_UP_QUEUED = "follow_up_queued"
    CANCELLED = "cancelled"
    LIMIT_REACHED = "limit_reached"


class RuntimeEvent(ProtocolModel):
    """Correlated and serializable event for UI, logs, and audit sinks."""

    type: RuntimeEventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    schema_version: Literal[1] = 1
    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    task_id: str | None = Field(default=None, min_length=1)
    turn_id: str | None = Field(default=None, min_length=1)
    model_call_id: str | None = Field(default=None, min_length=1)
    tool_call_id: str | None = Field(default=None, min_length=1)
    parent_run_id: str | None = Field(default=None, min_length=1)
    payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        """Normalize timestamps to UTC and reject naive datetimes."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_correlations(self) -> Self:
        """Require correlation identifiers associated with each lifecycle scope."""
        turn_events = {
            RuntimeEventType.TURN_STARTED,
            RuntimeEventType.TURN_COMPLETED,
            RuntimeEventType.CONTEXT_COMPACTED,
        }
        model_events = {
            RuntimeEventType.MODEL_STARTED,
            RuntimeEventType.MODEL_DELTA,
            RuntimeEventType.MODEL_COMPLETED,
        }
        tool_events = {
            RuntimeEventType.TOOL_REQUESTED,
            RuntimeEventType.PERMISSION_REQUESTED,
            RuntimeEventType.TOOL_STARTED,
            RuntimeEventType.TOOL_COMPLETED,
        }
        if self.type in turn_events and self.turn_id is None:
            raise ValueError(f"{self.type.value} event requires turn_id")
        if self.type in model_events and (self.turn_id is None or self.model_call_id is None):
            raise ValueError(f"{self.type.value} event requires turn_id and model_call_id")
        if self.type in tool_events and (self.turn_id is None or self.tool_call_id is None):
            raise ValueError(f"{self.type.value} event requires turn_id and tool_call_id")
        return self
