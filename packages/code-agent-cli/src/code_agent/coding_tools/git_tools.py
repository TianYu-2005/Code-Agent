"""Builtin git_status and git_diff coding tools."""

import asyncio
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

from .common import spec_for

STATUS_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

DIFF_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "Optional path to limit the diff"},
    },
    "additionalProperties": False,
}


async def _run_git(context: ExecutionContext, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=context.workspace,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await process.communicate()
    return stdout.decode("utf-8", errors="replace")


class GitStatusTool:
    """Show the working tree status."""

    def __init__(self) -> None:
        self.spec: ToolSpec = spec_for(
            "git_status",
            "Show the git working tree status.",
            STATUS_SCHEMA,
            effects=frozenset({ToolEffect.READ}),
        )

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        return (
            ToolTarget(
                effect=ToolEffect.READ,
                resource="git:status",
            ),
        )

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: Any,
    ) -> ToolOutcome:
        text = await _run_git(context, "status", "--short")
        output.write(text or "Working tree clean.\n")
        return ToolOutcome()

    async def abort(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        reason: Any,
    ) -> None:
        return None


class GitDiffTool:
    """Show the git diff of the working tree."""

    def __init__(self) -> None:
        self.spec: ToolSpec = spec_for(
            "git_diff",
            "Show the git diff of the working tree, optionally limited to a path.",
            DIFF_SCHEMA,
            effects=frozenset({ToolEffect.READ}),
        )

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        return (
            ToolTarget(
                effect=ToolEffect.READ,
                resource="git:diff",
            ),
        )

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: Any,
    ) -> ToolOutcome:
        args = ["diff"]
        path = call.arguments.get("path")
        if path:
            args.append(str(path))
        text = await _run_git(context, *args)
        output.write(text or "No changes.\n")
        return ToolOutcome()

    async def abort(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        reason: Any,
    ) -> None:
        return None
