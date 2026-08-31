"""Builtin run_command coding tool."""

import asyncio
import os
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

from .common import command_target, spec_for
from .workspace import WorkspaceError

SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Command and arguments to run (argv, no shell)",
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}

ALLOWED_ENV = {"PATH", "HOME", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE", "VIRTUAL_ENV"}


class RunCommandTool:
    """Run a command with argv (no shell) inside the workspace."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self.spec: ToolSpec = spec_for(
            "run_command",
            "Run a shell command as argv (no shell) with a timeout; use for tests and builds.",
            SCHEMA,
            effects=frozenset({ToolEffect.EXECUTE}),
            timeout=timeout,
            concurrency_key="command",
        )
        self._process: asyncio.subprocess.Process | None = None

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        argv = self._argv(arguments)
        if not argv:
            from .workspace import WorkspaceError

            raise WorkspaceError("command must not be empty")
        return (command_target(tuple(argv)),)

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: Any,
    ) -> ToolOutcome:
        argv = self._argv(call.arguments)
        env = {key: os.environ[key] for key in ALLOWED_ENV if key in os.environ}
        self._process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=context.workspace,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        stdout, _ = await self._process.communicate()
        exit_code = self._process.returncode or 0
        text = stdout.decode("utf-8", errors="replace")
        tail = text[-4000:] if len(text) > 4000 else text
        output.write(f"$ {' '.join(argv)}\n{tail}\n[exit code: {exit_code}]\n")
        return ToolOutcome(metadata={"exit_code": exit_code})

    @staticmethod
    def _argv(arguments: Mapping[str, JsonValue]) -> list[str]:
        """Extract a string argv list from validated arguments."""
        raw = arguments.get("command")
        if not isinstance(raw, list):
            raise WorkspaceError("command must be an array of strings")
        return [str(item) for item in raw]

    async def abort(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        reason: Any,
    ) -> None:
        if self._process is not None and self._process.returncode is None:
            try:
                os.killpg(self._process.pid, 15)
            except ProcessLookupError:
                pass
            self._process = None
