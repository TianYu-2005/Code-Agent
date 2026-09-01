"""Builtin run_command coding tool."""

import asyncio
import os
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from pydantic import JsonValue

from code_agent_core import (
    ToolEffect,
    ToolOutcome,
    ToolOutputSink,
    ToolSpec,
    ToolTarget,
    ValidatedToolCall,
)
from code_agent_core.runtime.spec import ExecutionContext

from .common import (
    command_environment,
    command_target,
    normalize_argv_field,
    spec_for,
)
from .workspace import WorkspaceError, resolve_workspace_path

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 600

SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": (
                "Command argv as a JSON array of strings, never a shell string. "
                'Example: ["npm", "install"].'
            ),
        },
        "cwd": {
            "type": "string",
            "description": "Optional workspace-relative working directory",
        },
        "timeout_seconds": {
            "type": "number",
            "minimum": 1,
            "maximum": MAX_TIMEOUT_SECONDS,
            "description": (
                "Per-call timeout. Defaults to 30 seconds; use a larger value for installs, "
                "builds, and test suites. Do not use this tool for persistent servers."
            ),
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}


class CommandExecutionError(RuntimeError):
    """Raised when a command exits unsuccessfully."""


class RunCommandTool:
    """Run a finite command as argv inside the workspace."""

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.spec: ToolSpec = spec_for(
            "run_command",
            (
                "Run a finite command as argv (no shell) for tests, builds, and installs. "
                'Pass command as an array, e.g. {"command":["npm","install"]}. '
                "Use start_process for persistent development servers."
            ),
            SCHEMA,
            effects=frozenset({ToolEffect.EXECUTE}),
            timeout=timeout,
            concurrency_key="command",
        )
        self._process: asyncio.subprocess.Process | None = None

    def normalize_arguments_json(self, arguments_json: str) -> str:
        """Repair a model-generated JSON string containing an encoded argv array."""
        return normalize_argv_field(arguments_json)

    def resolve_timeout_seconds(
        self,
        arguments: Mapping[str, JsonValue],
        default: float,
    ) -> float:
        """Use the validated per-call timeout when supplied."""
        value = arguments.get("timeout_seconds")
        return (
            float(value)
            if isinstance(value, int | float) and not isinstance(value, bool)
            else default
        )

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        argv = self._argv(arguments)
        cwd = self._cwd(arguments, context)
        target = command_target(tuple(argv))
        return (target.model_copy(update={"resource": f"{target.resource} (cwd: {cwd})"}),)

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: ToolOutputSink,
    ) -> ToolOutcome:
        argv = self._argv(call.arguments)
        cwd = self._cwd(call.arguments, context)
        self._process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=command_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        stdout, _ = await self._process.communicate()
        exit_code = self._process.returncode or 0
        text = stdout.decode("utf-8", errors="replace")
        tail = text[-4000:] if len(text) > 4000 else text
        transcript = f"$ {' '.join(argv)}\n{tail}\n[exit code: {exit_code}]\n"
        if exit_code != 0:
            raise CommandExecutionError(transcript)
        output.write(transcript)
        return ToolOutcome(metadata={"exit_code": exit_code, "cwd": str(cwd)})

    @staticmethod
    def _argv(arguments: Mapping[str, JsonValue]) -> list[str]:
        raw = arguments.get("command")
        if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
            raise WorkspaceError("command must be a non-empty array of strings")
        return list(cast(list[str], raw))

    @staticmethod
    def _cwd(arguments: Mapping[str, JsonValue], context: ExecutionContext) -> Path:
        raw = arguments.get("cwd", ".")
        if not isinstance(raw, str):
            raise WorkspaceError("cwd must be a workspace-relative string")
        path = resolve_workspace_path(raw, context.workspace)
        if not path.is_dir():
            raise WorkspaceError(f"command working directory does not exist: {raw}")
        return path

    async def abort(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        reason: object,
    ) -> None:
        if self._process is not None and self._process.returncode is None:
            try:
                os.killpg(self._process.pid, 15)
            except ProcessLookupError:
                pass
            self._process = None
