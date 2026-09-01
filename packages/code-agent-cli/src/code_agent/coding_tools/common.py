"""Shared helpers for builtin coding tools."""

import json
import os
from pathlib import Path

from pydantic import JsonValue

from code_agent_core import ToolEffect, ToolSpec, ToolTarget
from code_agent_core.runtime.spec import ExecutionContext

from .workspace import is_sensitive, resolve_workspace_path

ALLOWED_COMMAND_ENV = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "PYTHONDONTWRITEBYTECODE",
    "VIRTUAL_ENV",
}


def command_environment() -> dict[str, str]:
    """Return the small inherited environment exposed to child processes."""
    return {key: os.environ[key] for key in ALLOWED_COMMAND_ENV if key in os.environ}


def read_target(path: str, context: ExecutionContext) -> ToolTarget:
    """Resolve a read-only target, marking sensitive files."""
    resolved = resolve_workspace_path(path, context.workspace)
    return ToolTarget(
        effect=ToolEffect.READ,
        resource=str(resolved),
        sensitive=is_sensitive(resolved),
    )


def write_target(path: str, context: ExecutionContext) -> ToolTarget:
    """Resolve a write target inside the workspace."""
    resolved = resolve_workspace_path(path, context.workspace)
    return ToolTarget(
        effect=ToolEffect.WRITE,
        resource=str(resolved),
        sensitive=is_sensitive(resolved),
    )


def command_target(argv: tuple[str, ...]) -> ToolTarget:
    """Describe a command execution target."""
    return ToolTarget(
        effect=ToolEffect.EXECUTE,
        resource=" ".join(argv[:8]),
    )


def normalize_argv_field(arguments_json: str, field: str = "command") -> str:
    """Repair a JSON-encoded argv string while leaving all other inputs unchanged.

    Some OpenAI-compatible models occasionally emit ``command`` as the string
    ``'["npm", "install"]'`` instead of a JSON array. Only that exact,
    unambiguous shape is normalized; ordinary shell strings remain invalid.
    """
    try:
        value = json.loads(arguments_json)
    except (json.JSONDecodeError, RecursionError):
        return arguments_json
    if not isinstance(value, dict) or not isinstance(value.get(field), str):
        return arguments_json
    try:
        argv = json.loads(value[field])
    except (json.JSONDecodeError, RecursionError):
        return arguments_json
    if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
        return arguments_json
    value[field] = argv
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def spec_for(
    name: str,
    description: str,
    schema: dict[str, JsonValue],
    *,
    effects: frozenset[ToolEffect],
    timeout: float = 30.0,
    concurrency_key: str | None = None,
) -> ToolSpec:
    """Build a builtin tool specification."""
    return ToolSpec(
        name=name,
        description=description,
        input_schema=schema,
        effects=effects,
        timeout_seconds=timeout,
        concurrency_key=concurrency_key,
    )


def ensure_text_file(path: Path, max_bytes: int = 2_000_000) -> str:
    """Read a file as text, refusing oversized or binary content."""
    if not path.is_file():
        raise FileNotFoundError(path)
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"file is too large ({size} bytes)")
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError("file is binary")
    return data.decode("utf-8", errors="replace")
