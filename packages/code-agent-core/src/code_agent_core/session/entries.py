"""Versioned tree-session entry contracts."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from code_agent_llm.types import Message, ProtocolModel

from ..runtime.events import RuntimeEvent  # noqa: F401  (re-exported for payloads)


class SessionEntryType(StrEnum):
    """Kinds of facts persisted in a session tree."""

    MESSAGE = "message"
    RUN_EVENT = "run_event"
    COMPACTION = "compaction"
    MEMORY = "memory"


class MessageEntryPayload(ProtocolModel):
    """Payload for a persisted conversation message."""

    message: Message


class RunEventEntryPayload(ProtocolModel):
    """Payload for a persisted runtime event."""

    event: RuntimeEvent


class CompactionEntryPayload(ProtocolModel):
    """Payload describing a summary of earlier session entries."""

    summary: str = Field(min_length=1)
    source_entry_ids: tuple[str, ...] = Field(min_length=1)
    branch_head_id: str = Field(min_length=1)
    model: str = Field(min_length=1)

    @field_validator("source_entry_ids")
    @classmethod
    def validate_source_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Require non-empty and unique source identifiers."""
        if any(not entry_id for entry_id in value):
            raise ValueError("source entry ids cannot be empty")
        if len(value) != len(set(value)):
            raise ValueError("source entry ids must be unique")
        return value


class MemoryEntryPayload(ProtocolModel):
    """Payload recording a memory mutation in the session audit trail."""

    memory_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


SessionPayload = (
    MessageEntryPayload | RunEventEntryPayload | CompactionEntryPayload | MemoryEntryPayload
)


class SessionEntry(ProtocolModel):
    """One immutable node in a persistent session tree."""

    id: str = Field(min_length=1)
    parent_id: str | None = Field(default=None, min_length=1)
    type: SessionEntryType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: SessionPayload
    schema_version: Literal[1] = 1

    @field_validator("timestamp")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        """Normalize timestamps to UTC and reject naive datetimes."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_tree_node(self) -> Self:
        """Validate the parent edge and payload associated with this node."""
        if self.id == self.parent_id:
            raise ValueError("session entry cannot be its own parent")
        expected_payload = {
            SessionEntryType.MESSAGE: MessageEntryPayload,
            SessionEntryType.RUN_EVENT: RunEventEntryPayload,
            SessionEntryType.COMPACTION: CompactionEntryPayload,
            SessionEntryType.MEMORY: MemoryEntryPayload,
        }[self.type]
        if not isinstance(self.payload, expected_payload):
            raise ValueError(f"{self.type.value} entry has an incompatible payload")
        return self
