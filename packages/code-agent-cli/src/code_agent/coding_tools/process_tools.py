"""Lifecycle tools for persistent development servers and workers."""

import asyncio
import os
import signal
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
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

from .common import command_environment, command_target, normalize_argv_field, spec_for
from .workspace import WorkspaceError, resolve_workspace_path

COMMAND_SCHEMA: dict[str, JsonValue] = {
    "type": "array",
    "items": {"type": "string"},
    "minItems": 1,
    "description": 'Command argv as a JSON array, e.g. ["npm", "run", "dev"]',
}
PROCESS_ID_SCHEMA: dict[str, JsonValue] = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "description": "Process identifier returned by start_process",
}

START_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "command": COMMAND_SCHEMA,
        "cwd": {
            "type": "string",
            "description": "Optional workspace-relative working directory",
        },
        "name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 64,
            "description": "Optional human-readable process name",
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}
STATUS_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {"process_id": PROCESS_ID_SCHEMA},
    "required": ["process_id"],
    "additionalProperties": False,
}
READ_SCHEMA: dict[str, JsonValue] = {
    "type": "object",
    "properties": {
        "process_id": PROCESS_ID_SCHEMA,
        "tail_lines": {
            "type": "integer",
            "minimum": 1,
            "maximum": 1000,
            "description": "Number of recent log lines to return (default 100)",
        },
    },
    "required": ["process_id"],
    "additionalProperties": False,
}


@dataclass(slots=True)
class ManagedProcess:
    """One process launched by the current Agent runtime."""

    process_id: str
    name: str
    argv: tuple[str, ...]
    cwd: Path
    log_path: Path
    process: asyncio.subprocess.Process
    started_at: datetime


class ProcessManager:
    """In-memory registry for persistent subprocesses and their log files."""

    def __init__(self) -> None:
        self._processes: dict[str, ManagedProcess] = {}

    async def start(
        self,
        argv: list[str],
        *,
        cwd: Path,
        workspace: Path,
        name: str | None,
    ) -> ManagedProcess:
        process_id = f"proc-{uuid.uuid4().hex[:8]}"
        log_dir = workspace / ".code-agent" / "processes"
        log_dir.mkdir(parents=True, exist_ok=True)
        _ensure_processes_ignored(workspace)
        log_path = log_dir / f"{process_id}.log"
        with log_path.open("ab", buffering=0) as stream:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=command_environment(),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=stream,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        managed = ManagedProcess(
            process_id=process_id,
            name=name or Path(argv[0]).name,
            argv=tuple(argv),
            cwd=cwd,
            log_path=log_path,
            process=process,
            started_at=datetime.now(UTC),
        )
        self._processes[process_id] = managed
        return managed

    def get(self, process_id: str) -> ManagedProcess:
        try:
            return self._processes[process_id]
        except KeyError as error:
            raise WorkspaceError(
                f"unknown process id: {process_id}; process ids are valid for this Agent runtime"
            ) from error

    async def stop(self, process_id: str, grace_seconds: float = 3.0) -> ManagedProcess:
        managed = self.get(process_id)
        process = managed.process
        if process.returncode is not None:
            return managed
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return managed
        try:
            await asyncio.wait_for(process.wait(), timeout=grace_seconds)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        return managed


class StartProcessTool:
    """Start a persistent process without waiting for it to exit."""

    def __init__(self, manager: ProcessManager) -> None:
        self._manager = manager
        self.spec: ToolSpec = spec_for(
            "start_process",
            (
                "Start a persistent development server or worker as argv and return immediately. "
                "Use process_status/read_process_output/stop_process to manage it."
            ),
            START_SCHEMA,
            effects=frozenset({ToolEffect.EXECUTE}),
            timeout=10,
            concurrency_key="process",
        )

    def normalize_arguments_json(self, arguments_json: str) -> str:
        return normalize_argv_field(arguments_json)

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        argv = _argv(arguments)
        cwd = _cwd(arguments, context)
        target = command_target(tuple(argv))
        return (target.model_copy(update={"resource": f"{target.resource} (cwd: {cwd})"}),)

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: ToolOutputSink,
    ) -> ToolOutcome:
        argv = _argv(call.arguments)
        cwd = _cwd(call.arguments, context)
        raw_name = call.arguments.get("name")
        name = raw_name if isinstance(raw_name, str) else None
        managed = await self._manager.start(
            argv,
            cwd=cwd,
            workspace=context.workspace,
            name=name,
        )
        # Give immediate startup failures a chance to surface.
        await asyncio.sleep(0.1)
        state = "running" if managed.process.returncode is None else "exited"
        output.write(
            f"Started {managed.name} as {managed.process_id}\n"
            f"PID: {managed.process.pid}\n"
            f"Status: {state}\n"
            f"Log: {managed.log_path}\n"
        )
        return ToolOutcome(
            metadata={
                "process_id": managed.process_id,
                "pid": managed.process.pid,
                "status": state,
                "log_path": str(managed.log_path),
            }
        )

    async def abort(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        reason: object,
    ) -> None:
        return None


class ProcessStatusTool:
    """Inspect a process started by start_process."""

    def __init__(self, manager: ProcessManager) -> None:
        self._manager = manager
        self.spec = spec_for(
            "process_status",
            "Get status, PID, command, cwd, and log path for a managed process.",
            STATUS_SCHEMA,
            effects=frozenset({ToolEffect.READ}),
        )

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        return (_process_target(arguments, ToolEffect.READ),)

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: ToolOutputSink,
    ) -> ToolOutcome:
        managed = self._manager.get(_process_id(call.arguments))
        return_code = managed.process.returncode
        status = "running" if return_code is None else "exited"
        output.write(
            f"Process: {managed.process_id} ({managed.name})\n"
            f"Status: {status}\n"
            f"PID: {managed.process.pid}\n"
            f"Exit code: {return_code if return_code is not None else '-'}\n"
            f"Command: {' '.join(managed.argv)}\n"
            f"Cwd: {managed.cwd}\n"
            f"Log: {managed.log_path}\n"
        )
        return ToolOutcome(metadata={"status": status, "exit_code": return_code})

    async def abort(
        self, call: ValidatedToolCall, context: ExecutionContext, reason: object
    ) -> None:
        return None


class ReadProcessOutputTool:
    """Read recent output from a managed process log."""

    def __init__(self, manager: ProcessManager) -> None:
        self._manager = manager
        self.spec = spec_for(
            "read_process_output",
            "Read the latest lines written by a process started with start_process.",
            READ_SCHEMA,
            effects=frozenset({ToolEffect.READ}),
        )

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        managed = self._manager.get(_process_id(arguments))
        return (ToolTarget(effect=ToolEffect.READ, resource=str(managed.log_path)),)

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: ToolOutputSink,
    ) -> ToolOutcome:
        managed = self._manager.get(_process_id(call.arguments))
        raw_lines = call.arguments.get("tail_lines", 100)
        tail_lines = (
            raw_lines if isinstance(raw_lines, int) and not isinstance(raw_lines, bool) else 100
        )
        try:
            text = managed.log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            text = ""
        lines = text.splitlines()
        tail = "\n".join(lines[-tail_lines:])
        status = "running" if managed.process.returncode is None else "exited"
        output.write(f"Process: {managed.process_id} ({status})\nLog: {managed.log_path}\n{tail}\n")
        return ToolOutcome(metadata={"lines": min(len(lines), tail_lines)})

    async def abort(
        self, call: ValidatedToolCall, context: ExecutionContext, reason: object
    ) -> None:
        return None


class StopProcessTool:
    """Stop a process started by start_process."""

    def __init__(self, manager: ProcessManager) -> None:
        self._manager = manager
        self.spec = spec_for(
            "stop_process",
            "Stop a process started with start_process (SIGTERM, then SIGKILL if needed).",
            STATUS_SCHEMA,
            effects=frozenset({ToolEffect.EXECUTE}),
            timeout=10,
            concurrency_key="process",
        )

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        return (_process_target(arguments, ToolEffect.EXECUTE),)

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: ToolOutputSink,
    ) -> ToolOutcome:
        managed = await self._manager.stop(_process_id(call.arguments))
        output.write(
            f"Stopped {managed.process_id} ({managed.name}); "
            f"exit code: {managed.process.returncode}\n"
        )
        return ToolOutcome(metadata={"exit_code": managed.process.returncode})

    async def abort(
        self, call: ValidatedToolCall, context: ExecutionContext, reason: object
    ) -> None:
        return None


def default_process_tools(manager: ProcessManager) -> tuple[object, ...]:
    """Build lifecycle tools sharing one runtime-scoped process registry."""
    return (
        StartProcessTool(manager),
        ProcessStatusTool(manager),
        ReadProcessOutputTool(manager),
        StopProcessTool(manager),
    )


def _argv(arguments: Mapping[str, JsonValue]) -> list[str]:
    raw = arguments.get("command")
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
        raise WorkspaceError("command must be a non-empty array of strings")
    return list(cast(list[str], raw))


def _cwd(arguments: Mapping[str, JsonValue], context: ExecutionContext) -> Path:
    raw = arguments.get("cwd", ".")
    if not isinstance(raw, str):
        raise WorkspaceError("cwd must be a workspace-relative string")
    path = resolve_workspace_path(raw, context.workspace)
    if not path.is_dir():
        raise WorkspaceError(f"process working directory does not exist: {raw}")
    return path


def _process_id(arguments: Mapping[str, JsonValue]) -> str:
    raw = arguments.get("process_id")
    if not isinstance(raw, str) or not raw:
        raise WorkspaceError("process_id must be a non-empty string")
    return raw


def _process_target(arguments: Mapping[str, JsonValue], effect: ToolEffect) -> ToolTarget:
    return ToolTarget(effect=effect, resource=f"process:{_process_id(arguments)}")


def _ensure_processes_ignored(workspace: Path) -> None:
    code_agent_dir = workspace / ".code-agent"
    code_agent_dir.mkdir(parents=True, exist_ok=True)
    ignore_path = code_agent_dir / ".gitignore"
    existing = ignore_path.read_text(encoding="utf-8") if ignore_path.exists() else ""
    lines = existing.splitlines()
    if "processes/" not in lines:
        with ignore_path.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write("processes/\n")
