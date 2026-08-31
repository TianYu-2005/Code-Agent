"""Terminal user interface for the coding agent."""

from .approval import TerminalApprovalPort
from .commands import parse_input
from .interrupt import CancelState
from .renderer import TerminalRenderer


def __getattr__(name: str) -> object:
    """Lazily import the app module to avoid a bootstrap import cycle."""
    if name in {"main", "run_app"}:
        from .app import main, run_app

        return {"main": main, "run_app": run_app}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CancelState",
    "TerminalApprovalPort",
    "TerminalRenderer",
    "main",
    "parse_input",
    "run_app",
]
