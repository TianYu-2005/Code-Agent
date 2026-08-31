"""Builtin read_file coding tool."""

from collections.abc import Mapping
from typing import Any

from pydantic import JsonValue

from code_agent_core import (
    ToolEffect,
    ToolOutcome,
    ToolSpec,
    ToolTarget,
    ValidatedToolCall,
)
from code_agent_core.runtime.spec import ExecutionContext

from .common import read_target, spec_for
from .workspace import format_output

SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Workspace-relative file path"},
        "offset": {
            "type": "integer",
            "description": "1-based line to start from",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum lines to read",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


def _int_arg(arguments: dict[str, JsonValue], key: str, default: int) -> int:
    value = arguments.get(key, default)
    return value if isinstance(value, int) else default


class ReadFileTool:
    """Read a text file with line numbers."""

    def __init__(self) -> None:
        self.spec: ToolSpec = spec_for(
            "read_file",
            "Read a text file from the workspace with line numbers.",
            SCHEMA,
            effects=frozenset({ToolEffect.READ}),
        )

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        path = str(arguments["path"])
        return (read_target(path, context),)

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: Any,
    ) -> ToolOutcome:
        from .common import ensure_text_file
        from .workspace import resolve_workspace_path

        path = resolve_workspace_path(str(call.arguments["path"]), context.workspace)
        content = ensure_text_file(path)
        offset = _int_arg(call.arguments, "offset", 1)
        limit = _int_arg(call.arguments, "limit", 400)
        lines = content.splitlines()
        selected = lines[offset - 1 : offset - 1 + limit]
        numbered = "\n".join(
            f"{index}: {line}" for index, line in enumerate(selected, start=offset)
        )
        formatted, _ = format_output(numbered, max_lines=limit)
        header = f"{path} (lines {offset}-{offset + len(selected) - 1} of {len(lines)})"
        output.write(f"{header}\n{formatted}\n")
        return ToolOutcome(metadata={"path": str(path), "lines": len(selected)})

    async def abort(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        reason: Any,
    ) -> None:
        return None
