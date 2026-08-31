"""Workspace path resolution and boundary enforcement."""

from pathlib import Path

SENSITIVE_PATTERNS = (
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "secrets.yaml",
    "secrets.yml",
)


class WorkspaceError(ValueError):
    """Raised when a path violates workspace rules."""


def is_sensitive(path: Path) -> bool:
    """Return whether a path looks like a credential or secret file."""
    name = path.name.lower()
    return name in SENSITIVE_PATTERNS or (name.startswith(".env") and not name == ".env.example")


def resolve_workspace_path(raw: str, workspace: Path) -> Path:
    """Resolve a user-supplied path inside the workspace boundary."""
    candidate = Path(raw)
    absolute = candidate if candidate.is_absolute() else workspace / candidate
    resolved = absolute.resolve()
    workspace_resolved = workspace.resolve()
    if resolved != workspace_resolved and workspace_resolved not in resolved.parents:
        raise WorkspaceError(f"path escapes the workspace: {raw}")
    return resolved


def format_output(content: str, max_lines: int = 400) -> tuple[str, bool]:
    """Format text output with a line cap, reporting truncation."""
    lines = content.splitlines()
    if len(lines) <= max_lines:
        return content, False
    kept = lines[:max_lines]
    return "\n".join(kept) + f"\n... ({len(lines) - max_lines} more lines)", True
