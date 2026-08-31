"""Builtin write_file coding tool."""

import os
import tempfile
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

from .common import spec_for, write_target
from .workspace import resolve_workspace_path

SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File path to write"},
        "content": {"type": "string", "description": "Full file content"},
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}


class WriteFileTool:
    """Create or overwrite a file inside the workspace."""

    def __init__(self) -> None:
        self.spec: ToolSpec = spec_for(
            "write_file",
            "Create a new file or overwrite an existing one with full content.",
            SCHEMA,
            effects=frozenset({ToolEffect.WRITE}),
            concurrency_key="file-write",
        )

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        return (write_target(str(arguments["path"]), context),)

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: Any,
    ) -> ToolOutcome:
        path = resolve_workspace_path(str(call.arguments["path"]), context.workspace)
        content = str(call.arguments["content"])
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(dir=path.parent, prefix=".code-agent-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
        except BaseException:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
            raise
        action = "overwrote" if existed else "created"
        output.write(f"{action.capitalize()} {path} ({len(content)} chars).\n")
        return ToolOutcome(metadata={"path": str(path), "created": not existed})

    async def abort(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        reason: Any,
    ) -> None:
        return None
