"""Agent runtime: run specs, events, and the agent loop."""

from code_agent_llm.provider import CancellationToken

from .events import RuntimeEvent, RuntimeEventType
from .spec import (
    EventSink,
    ExecutionContext,
    PermissionContext,
    RunBudgets,
    RunSpec,
)

__all__ = [
    "AgentLoop",
    "CancellationToken",
    "EventSink",
    "ExecutionContext",
    "LoopEndReason",
    "LoopResult",
    "PermissionContext",
    "RunBudgets",
    "RunSpec",
    "RuntimeEvent",
    "RuntimeEventType",
]


def __getattr__(name: str) -> object:
    """Lazily import the agent loop to avoid import cycles."""
    if name in {"AgentLoop", "LoopEndReason", "LoopResult"}:
        from .loop import AgentLoop, LoopEndReason, LoopResult

        return {
            "AgentLoop": AgentLoop,
            "LoopEndReason": LoopEndReason,
            "LoopResult": LoopResult,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
