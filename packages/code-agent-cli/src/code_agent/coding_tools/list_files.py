"""Builtin list_files coding tool."""

from collections.abc import Mapping
from pathlib import Path
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

from .common import spec_for
from .workspace import resolve_workspace_path

IGNORED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".DS_Store",
}

SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Directory to list relative to the workspace",
        },
        "glob": {
            "type": "string",
            "description": "Optional glob like '*.py' to filter names",
        },
    },
    "additionalProperties": False,
}


class ListFilesTool:
    """List files under a workspace directory."""

    def __init__(self) -> None:
        self.spec: ToolSpec = spec_for(
            "list_files",
            "List files in a workspace directory, skipping common ignored folders.",
            SCHEMA,
            effects=frozenset({ToolEffect.READ}),
        )

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        path = str(arguments.get("path", "."))
        from .common import read_target

        return (read_target(path, context),)

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: Any,
    ) -> ToolOutcome:
        directory = resolve_workspace_path(str(call.arguments.get("path", ".")), context.workspace)
        pattern = str(call.arguments.get("glob", "*"))
        if not directory.is_dir():
            raise FileNotFoundError(directory)
        matches: list[str] = []
        for item in sorted(directory.iterdir()):
            if item.name in IGNORED_DIRS:
                continue
            if item.is_file() and Path(item.name).match(pattern):
                matches.append(item.name)
            elif item.is_dir():
                matches.append(f"{item.name}/")
        if not matches:
            output.write("No matching files.\n")
        else:
            output.write("\n".join(matches[:200]))
            if len(matches) > 200:
                output.write(f"\n... and {len(matches) - 200} more")
            output.write("\n")
        return ToolOutcome(metadata={"count": len(matches)})

    async def abort(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        reason: Any,
    ) -> None:
        return None
