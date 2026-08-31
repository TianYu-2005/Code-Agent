"""Provider-independent runtime for Code Agent."""

from code_agent_llm.provider import CancellationToken

from .events import RuntimeEvent, RuntimeEventType
from .runtime import (
    EventSink,
    ExecutionContext,
    PermissionContext,
    RunBudgets,
    RunSpec,
)
from .session import (
    CompactionEntryPayload,
    MemoryEntryPayload,
    MessageEntryPayload,
    RunEventEntryPayload,
    SessionEntry,
    SessionEntryType,
    SessionPayload,
)
from .tools import (
    MAX_TOOL_RESULT_BYTES,
    MAX_TOOL_RESULT_CHARS,
    ToolEffect,
    ToolOrigin,
    ToolResult,
    ToolSpec,
    ToolStatus,
)

__version__ = "0.1.0"

__all__ = [
    "MAX_TOOL_RESULT_BYTES",
    "MAX_TOOL_RESULT_CHARS",
    "CancellationToken",
    "CompactionEntryPayload",
    "EventSink",
    "ExecutionContext",
    "MemoryEntryPayload",
    "MessageEntryPayload",
    "PermissionContext",
    "RunBudgets",
    "RunEventEntryPayload",
    "RunSpec",
    "RuntimeEvent",
    "RuntimeEventType",
    "SessionEntry",
    "SessionEntryType",
    "SessionPayload",
    "ToolEffect",
    "ToolOrigin",
    "ToolResult",
    "ToolSpec",
    "ToolStatus",
    "__version__",
]
