"""Builtin replace_in_file coding tool."""

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

from .common import ensure_text_file, spec_for, write_target
from .workspace import resolve_workspace_path

SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File to edit"},
        "old_text": {"type": "string", "description": "Text to replace"},
        "new_text": {"type": "string", "description": "Replacement text"},
    },
    "required": ["path", "old_text", "new_text"],
    "additionalProperties": False,
}


class ReplaceInFileTool:
    """Replace exactly one occurrence of text in a file."""

    def __init__(self) -> None:
        self.spec: ToolSpec = spec_for(
            "replace_in_file",
            "Replace a unique text snippet in a file; fails unless it matches once.",
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
        old_text = str(call.arguments["old_text"])
        new_text = str(call.arguments["new_text"])
        content = ensure_text_file(path)
        count = content.count(old_text)
        if count == 0:
            raise ValueError("old_text not found in file")
        if count > 1:
            raise ValueError(f"old_text matches {count} times; provide more context")
        updated = content.replace(old_text, new_text, 1)
        _atomic_write(path, updated)
        output.write(f"Replaced one occurrence in {path}.\n")
        return ToolOutcome(metadata={"path": str(path), "replacements": 1})

    async def abort(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        reason: Any,
    ) -> None:
        return None


def _atomic_write(path: Any, content: str) -> None:
    directory = path.parent
    fd, temp_path = tempfile.mkstemp(dir=directory, prefix=".code-agent-", suffix=".tmp")
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
