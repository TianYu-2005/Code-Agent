"""Builtin search_text coding tool."""

import re
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
        "pattern": {"type": "string", "description": "Regex to search for"},
        "path": {
            "type": "string",
            "description": "File or directory to search, relative to workspace",
        },
        "glob": {
            "type": "string",
            "description": "Filename filter like '*.py'",
        },
    },
    "required": ["pattern"],
    "additionalProperties": False,
}

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
}


class SearchTextTool:
    """Search file contents with a regular expression."""

    def __init__(self) -> None:
        self.spec: ToolSpec = spec_for(
            "search_text",
            "Search workspace files with a regex and show matching lines.",
            SCHEMA,
            effects=frozenset({ToolEffect.READ}),
        )

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        path = str(arguments.get("path", "."))
        return (read_target(path, context),)

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: Any,
    ) -> ToolOutcome:
        from .workspace import resolve_workspace_path

        pattern = str(call.arguments["pattern"])
        try:
            regex = re.compile(pattern)
        except re.error as error:
            raise ValueError(f"invalid regex: {error}") from error
        root = resolve_workspace_path(str(call.arguments.get("path", ".")), context.workspace)
        glob = str(call.arguments.get("glob", "*"))
        matches: list[str] = []
        files = [root] if root.is_file() else sorted(root.rglob(glob))
        for file_path in files:
            if any(part in IGNORED_DIRS for part in file_path.parts):
                continue
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for line_number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    relative = file_path.relative_to(context.workspace)
                    matches.append(f"{relative}:{line_number}: {line.strip()[:200]}")
            if len(matches) >= 200:
                break
        formatted, truncated = format_output("\n".join(matches), max_lines=200)
        if not matches:
            output.write("No matches found.\n")
        else:
            output.write(formatted + "\n")
        return ToolOutcome(
            metadata={"matches": len(matches), "truncated": truncated},
        )

    async def abort(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        reason: Any,
    ) -> None:
        return None
