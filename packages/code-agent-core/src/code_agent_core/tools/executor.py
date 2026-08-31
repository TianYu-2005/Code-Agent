"""Single secure execution boundary for all tool invocations."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from hashlib import sha256
from math import isfinite
from typing import Any, TypeVar

from pydantic import JsonValue

from code_agent_llm import ToolCall

from ..runtime.spec import ExecutionContext
from .base import (
    ToolAbortReason,
    ToolEffect,
    ToolOutcome,
    ToolResult,
    ToolStatus,
    ToolTarget,
    ValidatedToolCall,
)
from .concurrency import ToolConcurrencyController
from .output import BoundedToolOutput
from .permissions import (
    ApprovalPort,
    ApprovalRequest,
    ApprovalResponse,
    PermissionAction,
    PermissionPolicy,
)
from .registry import RegisteredTool, ToolRegistry, ToolRegistryError
from .schema import validate_arguments

T = TypeVar("T")


class ToolTerminationError(RuntimeError):
    """Raised when an in-process tool or approval adapter cannot be stopped safely."""


class ToolExecutor:
    """Validate, authorize, serialize, limit, and execute tool calls."""

    def __init__(
        self,
        registry: ToolRegistry,
        permission_policy: PermissionPolicy,
        approval_port: ApprovalPort,
        *,
        concurrency: ToolConcurrencyController | None = None,
        approval_timeout_seconds: float = 300.0,
        abort_timeout_seconds: float = 2.0,
    ) -> None:
        timeouts = (approval_timeout_seconds, abort_timeout_seconds)
        if any(not isfinite(timeout) or timeout <= 0 for timeout in timeouts):
            raise ValueError("executor timeouts must be finite and positive")
        self._registry = registry
        self._permission_policy = permission_policy
        self._approval_port = approval_port
        self._concurrency = concurrency or ToolConcurrencyController(max_concurrency=1)
        self._approval_timeout = approval_timeout_seconds
        self._abort_timeout = abort_timeout_seconds

    async def execute(self, call: ToolCall, context: ExecutionContext) -> ToolResult:
        if context.cancellation.is_cancelled:
            return _result(ToolStatus.CANCELLED, "tool execution was cancelled")
        try:
            registered = self._registry.get(call.name)
        except ToolRegistryError:
            return _result(ToolStatus.ERROR, "unknown tool")

        arguments, validation_error = validate_arguments(call, registered.schema)
        if validation_error is not None:
            return _result(ToolStatus.ERROR, validation_error)

        try:
            targets = registered.tool.resolve_targets(arguments, context)
            validated = _validated_call(call, arguments, targets, registered, context)
            decision = self._permission_policy.decide(validated, registered.spec, context)
        except Exception:
            return _result(ToolStatus.ERROR, "tool authorization planning failed")

        if context.cancellation.is_cancelled:
            return _result(ToolStatus.CANCELLED, "tool execution was cancelled")
        if decision.action is PermissionAction.DENY:
            return _result(ToolStatus.DENIED, decision.reason)
        if decision.action is PermissionAction.ASK:
            approval = await self._request_approval(validated, registered, context)
            if isinstance(approval, ToolResult):
                return approval
            if approval.fingerprint != validated.fingerprint:
                return _result(ToolStatus.DENIED, "approval does not match this invocation")
            if not approval.approved:
                return _result(ToolStatus.DENIED, "user denied tool execution")

        return await self._execute_authorized(validated, registered, context)

    async def _request_approval(
        self,
        call: ValidatedToolCall,
        registered: RegisteredTool,
        context: ExecutionContext,
    ) -> ApprovalResponse | ToolResult:
        request = ApprovalRequest(call=call, description=registered.spec.description)
        status, value = await _race(
            self._approval_port.request(request, context),
            context,
            timeout_seconds=self._approval_timeout,
        )
        if status is ToolStatus.SUCCESS:
            if value is None:
                return _result(ToolStatus.ERROR, "tool approval returned no response")
            if isinstance(value, ApprovalResponse):
                return value
            return _result(ToolStatus.ERROR, str(value))
        if status is ToolStatus.TIMEOUT:
            return _result(ToolStatus.DENIED, "tool approval timed out")
        if status is ToolStatus.CANCELLED:
            return _result(ToolStatus.CANCELLED, "tool approval was cancelled")
        return _result(
            ToolStatus.ERROR, str(value) if isinstance(value, str) else "tool approval failed"
        )

    async def _execute_authorized(
        self,
        call: ValidatedToolCall,
        registered: RegisteredTool,
        context: ExecutionContext,
    ) -> ToolResult:
        output = BoundedToolOutput()
        keys = _concurrency_keys(call, registered)

        async def run() -> ToolOutcome:
            async with self._concurrency.acquire(keys):
                return await registered.tool.execute(call, context, output)

        async def interrupt(reason: ToolAbortReason) -> None:
            await self._abort(registered, call, context, reason)

        status, value = await _race(
            run(),
            context,
            timeout_seconds=registered.spec.timeout_seconds,
            on_interrupt=interrupt,
            termination_timeout_seconds=self._abort_timeout,
        )
        if status is ToolStatus.SUCCESS:
            if not isinstance(value, ToolOutcome):
                return _result(ToolStatus.ERROR, "tool returned an invalid outcome")
            return output.result(value)
        message = value if isinstance(value, str) else "tool execution failed"
        if status is ToolStatus.ERROR:
            return _result(ToolStatus.ERROR, message)
        if status is ToolStatus.TIMEOUT:
            return _result(ToolStatus.TIMEOUT, message or "tool execution timed out")
        if status is ToolStatus.CANCELLED:
            return _result(ToolStatus.CANCELLED, message or "tool execution was cancelled")
        return _result(ToolStatus.ERROR, message or "tool execution failed")

    async def _abort(
        self,
        registered: RegisteredTool,
        call: ValidatedToolCall,
        context: ExecutionContext,
        reason: ToolAbortReason,
    ) -> None:
        task = asyncio.create_task(registered.tool.abort(call, context, reason))
        done, _ = await asyncio.wait({task}, timeout=self._abort_timeout)
        if task not in done:
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=self._abort_timeout)
        if task not in done:
            task.add_done_callback(_consume_task_result)
            raise ToolTerminationError("tool abort hook could not be terminated safely")
        try:
            task.result()
        except (Exception, asyncio.CancelledError):
            raise ToolTerminationError("tool abort hook failed") from None


def _validated_call(
    call: ToolCall,
    arguments: dict[str, JsonValue],
    targets: tuple[ToolTarget, ...],
    registered: RegisteredTool,
    context: ExecutionContext,
) -> ValidatedToolCall:
    declared = set(registered.spec.effects)
    effective = declared | {target.effect for target in targets}
    canonical = json.dumps(
        {
            "id": call.id,
            "name": call.name,
            "session_id": context.session_id,
            "run_id": context.run_id,
            "arguments": arguments,
            "targets": [target.model_dump(mode="json") for target in targets],
            "effects": sorted(effect.value for effect in effective),
            "tool": registered.fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return ValidatedToolCall(
        id=call.id,
        name=call.name,
        arguments=arguments,
        targets=targets,
        effective_effects=frozenset(effective or {ToolEffect.UNKNOWN}),
        fingerprint=sha256(canonical.encode()).hexdigest(),
    )


def _concurrency_keys(
    call: ValidatedToolCall,
    registered: RegisteredTool,
) -> tuple[str, ...]:
    keys = [f"tool:{registered.spec.name}"]
    if registered.spec.concurrency_key:
        keys.append(f"declared:{registered.spec.concurrency_key}")
    keys.extend(f"resource:{target.resource}" for target in call.targets)
    return tuple(keys)


async def _race[T](
    operation: Awaitable[T],
    context: ExecutionContext,
    *,
    timeout_seconds: float,
    on_interrupt: Callable[[ToolAbortReason], Awaitable[None]] | None = None,
    termination_timeout_seconds: float = 2.0,
) -> tuple[ToolStatus, T | str | None]:
    if context.cancellation.is_cancelled:
        if asyncio.iscoroutine(operation):
            operation.close()
        return ToolStatus.CANCELLED, None
    operation_task = asyncio.ensure_future(operation)
    cancellation_task = asyncio.create_task(context.cancellation.wait())
    timeout_task = asyncio.create_task(asyncio.sleep(timeout_seconds))
    interrupted: ToolStatus | None = None
    try:
        done, _ = await asyncio.wait(
            {operation_task, cancellation_task, timeout_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if operation_task in done:
            if operation_task.cancelled():
                return ToolStatus.ERROR, "operation was cancelled"
            try:
                return ToolStatus.SUCCESS, await operation_task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                if current is not None and current.cancelling():
                    raise
                return ToolStatus.ERROR, "operation was cancelled"
            except Exception as exc:
                return ToolStatus.ERROR, f"{type(exc).__name__}: {exc}"
        interrupted = ToolStatus.CANCELLED if cancellation_task in done else ToolStatus.TIMEOUT
        reason = (
            ToolAbortReason.CANCELLED
            if interrupted is ToolStatus.CANCELLED
            else ToolAbortReason.TIMEOUT
        )
        await _terminate_operation(
            operation_task,
            on_interrupt,
            reason,
            termination_timeout_seconds,
        )
        return interrupted, None
    except asyncio.CancelledError:
        await asyncio.shield(
            _terminate_operation(
                operation_task,
                on_interrupt,
                ToolAbortReason.CALLER_CANCELLED,
                termination_timeout_seconds,
            )
        )
        raise
    finally:
        for task in (cancellation_task, timeout_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(cancellation_task, timeout_task, return_exceptions=True)


async def _terminate_operation[T](
    task: asyncio.Future[T],
    on_interrupt: Callable[[ToolAbortReason], Awaitable[None]] | None,
    reason: ToolAbortReason,
    timeout_seconds: float,
) -> None:
    task.cancel()
    try:
        if on_interrupt is not None:
            await on_interrupt(reason)
    except Exception:
        task.add_done_callback(_consume_task_result)
        raise
    done, _ = await asyncio.wait({task}, timeout=timeout_seconds)
    if task not in done:
        task.add_done_callback(_consume_task_result)
        raise ToolTerminationError("tool execution could not be terminated safely")
    await asyncio.gather(task, return_exceptions=True)


def _consume_task_result(task: asyncio.Future[Any]) -> None:
    try:
        task.result()
    except (Exception, asyncio.CancelledError):
        return


def _result(status: ToolStatus, content: str) -> ToolResult:
    return ToolResult(status=status, content=content[:1_024])
