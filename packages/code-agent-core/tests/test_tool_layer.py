import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import JsonValue

from code_agent_core import (
    ApprovalRequest,
    ApprovalResponse,
    BoundedToolOutput,
    DefaultPermissionPolicy,
    ExecutionContext,
    PermissionContext,
    ToolAbortReason,
    ToolConcurrencyController,
    ToolEffect,
    ToolExecutor,
    ToolOrigin,
    ToolOutcome,
    ToolOutputSink,
    ToolRegistry,
    ToolRegistryError,
    ToolResult,
    ToolSchemaError,
    ToolSpec,
    ToolStatus,
    ToolTarget,
    ToolTerminationError,
    ValidatedToolCall,
    compile_schema,
)
from code_agent_llm import ToolCall


@dataclass
class Token:
    event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def is_cancelled(self) -> bool:
        return self.event.is_set()

    async def wait(self) -> None:
        await self.event.wait()

    def cancel(self) -> None:
        self.event.set()


class Sink:
    async def emit(self, event: object) -> None:
        return None


class Approval:
    def __init__(self, approved: bool = True, *, mismatch: bool = False) -> None:
        self.approved = approved
        self.mismatch = mismatch
        self.requests: list[ApprovalRequest] = []

    async def request(
        self,
        request: ApprovalRequest,
        context: ExecutionContext,
    ) -> ApprovalResponse:
        self.requests.append(request)
        fingerprint = "0" * 64 if self.mismatch else request.call.fingerprint
        return ApprovalResponse(fingerprint=fingerprint, approved=self.approved)


class BlockingApproval(Approval):
    async def request(
        self,
        request: ApprovalRequest,
        context: ExecutionContext,
    ) -> ApprovalResponse:
        self.requests.append(request)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class TestTool:
    __test__ = False

    def __init__(
        self,
        *,
        effects: frozenset[ToolEffect] = frozenset({ToolEffect.READ}),
        origin: ToolOrigin = ToolOrigin.BUILTIN,
        target_effect: ToolEffect = ToolEffect.READ,
        resource: str = "workspace",
        external: bool = False,
        sensitive: bool = False,
        delay: float = 0,
        fail: bool = False,
        text: str = "ok",
        metadata: Mapping[str, JsonValue] | None = None,
        concurrency_key: str | None = None,
    ) -> None:
        self.spec = ToolSpec(
            name="test_tool",
            description="A test tool",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            effects=effects,
            timeout_seconds=0.05,
            concurrency_key=concurrency_key,
            origin=origin,
        )
        self.target_effect = target_effect
        self.resource = resource
        self.external = external
        self.sensitive = sensitive
        self.delay = delay
        self.fail = fail
        self.text = text
        self.metadata = metadata or {}
        self.executions = 0
        self.aborts: list[ToolAbortReason] = []
        self.active = 0
        self.max_active = 0

    def resolve_targets(
        self,
        arguments: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> tuple[ToolTarget, ...]:
        return (
            ToolTarget(
                effect=self.target_effect,
                resource=self.resource,
                external=self.external,
                sensitive=self.sensitive,
            ),
        )

    async def execute(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        output: ToolOutputSink,
    ) -> ToolOutcome:
        self.executions += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.fail:
                raise RuntimeError("secret implementation detail")
            output.write(self.text)
            return ToolOutcome(metadata=dict(self.metadata))
        finally:
            self.active -= 1

    async def abort(
        self,
        call: ValidatedToolCall,
        context: ExecutionContext,
        reason: ToolAbortReason,
    ) -> None:
        self.aborts.append(reason)


def make_context(tmp_path: Path, token: Token | None = None) -> ExecutionContext:
    return ExecutionContext(
        workspace=tmp_path,
        session_id="session-1",
        run_id="run-1",
        cancellation=token or Token(),
        permission_context=PermissionContext(),
        event_sink=Sink(),
    )


def make_call(call_id: str = "call-1", arguments: str = '{"value":"hello"}') -> ToolCall:
    return ToolCall(id=call_id, name="test_tool", arguments_json=arguments)


def _registry(
    tool: TestTool,
    origin: ToolOrigin | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(tool, origin=origin or tool.spec.origin)
    return registry


def make_executor(
    tool: TestTool,
    approval: Approval | None = None,
    *,
    concurrency: ToolConcurrencyController | None = None,
    approval_timeout: float = 0.05,
    registered_origin: ToolOrigin | None = None,
) -> ToolExecutor:
    return ToolExecutor(
        _registry(tool, registered_origin),
        DefaultPermissionPolicy(),
        approval or Approval(),
        concurrency=concurrency,
        approval_timeout_seconds=approval_timeout,
    )


def test_schema_compiler_allows_local_refs_and_rejects_external_refs() -> None:
    compiled = compile_schema(
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        }
    )
    compiled.validator.validate({"value": "ok"})
    with pytest.raises(ToolSchemaError, match="unsupported"):
        compile_schema(
            {
                "$defs": {"name": {"type": "string"}},
                "type": "object",
                "properties": {"value": {"$ref": "#/$defs/name"}},
                "additionalProperties": False,
            }
        )

    for reference in (
        "https://example.com/schema.json",
        "file:///etc/passwd",
        "http://127.0.0.1/schema.json",
    ):
        with pytest.raises(ToolSchemaError, match="unsupported"):
            compile_schema({"$ref": reference})
        with pytest.raises(ToolSchemaError, match="unsupported"):
            compile_schema({"$dynamicRef": reference})
    with pytest.raises(ToolSchemaError, match="identifiers"):
        compile_schema({"$id": "https://example.com/schema", "type": "object"})
    with pytest.raises(ToolSchemaError, match="undeclared"):
        compile_schema({"type": "object"})
    with pytest.raises(ToolSchemaError, match="Draft 2020-12"):
        compile_schema(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "unevaluatedProperties": False,
            }
        )
    with pytest.raises(ToolSchemaError, match="root tool schema"):
        compile_schema({"type": ["object"], "properties": {}})

    property_named_format = compile_schema(
        {
            "type": "object",
            "properties": {"format": {"type": "string"}},
            "additionalProperties": False,
        }
    )
    property_named_format.validator.validate({"format": "plain"})
    with pytest.raises(TypeError, match="cannot be modified"):
        cast(dict[str, Any], property_named_format.validator.schema)["type"] = "string"

    unconstrained_schemas: tuple[dict[str, JsonValue], ...] = (
        {
            "type": "object",
            "properties": {"payload": {}},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"payload": True},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"payload": {"type": "array"}},
            "additionalProperties": False,
        },
    )
    for unconstrained in unconstrained_schemas:
        with pytest.raises(ToolSchemaError):
            compile_schema(unconstrained)


def test_registry_rejects_duplicates_and_exports_model_specs() -> None:
    tool = TestTool()
    registry = ToolRegistry()
    registry.register(tool, origin=ToolOrigin.BUILTIN)

    assert registry.get("test_tool").tool is tool
    assert registry.names() == ("test_tool",)
    assert registry.model_specs()[0].name == "test_tool"
    with pytest.raises(ToolRegistryError, match="already registered"):
        registry.register(tool, origin=ToolOrigin.BUILTIN)
    with pytest.raises(ToolRegistryError, match="unknown tool"):
        registry.get("missing")


def test_executor_rejects_untrusted_arguments_without_running_tool(tmp_path: Path) -> None:
    tool = TestTool()
    executor = make_executor(tool)

    async def run() -> tuple[ToolResult, ToolResult, ToolResult, ToolResult, ToolResult]:
        invalid_json = await executor.execute(make_call(arguments="{"), make_context(tmp_path))
        invalid_shape = await executor.execute(make_call(arguments="[]"), make_context(tmp_path))
        invalid_schema = await executor.execute(
            make_call(arguments='{"other":1}'), make_context(tmp_path)
        )
        huge_integer = await executor.execute(
            make_call(arguments='{"value":' + "9" * 5_000 + "}"),
            make_context(tmp_path),
        )
        infinite_number = await executor.execute(
            make_call(arguments='{"value":1e999}'),
            make_context(tmp_path),
        )
        return invalid_json, invalid_shape, invalid_schema, huge_integer, infinite_number

    results = asyncio.run(run())
    assert all(result.status is ToolStatus.ERROR for result in results)
    assert tool.executions == 0


def test_effects_cannot_be_downgraded_by_dynamic_target(tmp_path: Path) -> None:
    tool = TestTool(
        effects=frozenset({ToolEffect.WRITE}),
        target_effect=ToolEffect.READ,
    )
    approval = Approval(approved=False)
    result = asyncio.run(make_executor(tool, approval).execute(make_call(), make_context(tmp_path)))

    assert result.status is ToolStatus.DENIED
    assert approval.requests[0].call.effective_effects == frozenset(
        {ToolEffect.WRITE, ToolEffect.READ}
    )
    assert tool.executions == 0


def test_default_policy_fails_closed_for_unknown_external_and_sensitive(tmp_path: Path) -> None:
    tools = (
        TestTool(effects=frozenset({ToolEffect.UNKNOWN})),
        TestTool(external=True),
        TestTool(sensitive=True),
    )

    for tool in tools:
        result = asyncio.run(make_executor(tool).execute(make_call(), make_context(tmp_path)))
        assert result.status is ToolStatus.DENIED
        assert tool.executions == 0


def test_builtin_read_is_allowed_but_external_origin_asks(tmp_path: Path) -> None:
    builtin = TestTool()
    builtin_approval = Approval(approved=False)
    builtin_result = asyncio.run(
        make_executor(builtin, builtin_approval).execute(make_call(), make_context(tmp_path))
    )
    external = TestTool(origin=ToolOrigin.BUILTIN)
    external_approval = Approval(approved=False)
    external_result = asyncio.run(
        make_executor(
            external,
            external_approval,
            registered_origin=ToolOrigin.MCP,
        ).execute(make_call(), make_context(tmp_path))
    )

    assert builtin_result.status is ToolStatus.SUCCESS
    assert builtin_approval.requests == []
    assert external_result.status is ToolStatus.DENIED
    assert len(external_approval.requests) == 1


def test_approval_is_bound_to_exact_invocation(tmp_path: Path) -> None:
    tool = TestTool(effects=frozenset({ToolEffect.WRITE}), target_effect=ToolEffect.WRITE)
    result = asyncio.run(
        make_executor(tool, Approval(mismatch=True)).execute(make_call(), make_context(tmp_path))
    )

    assert result.status is ToolStatus.DENIED
    assert tool.executions == 0


def test_approval_wait_is_cancellable_and_times_out(tmp_path: Path) -> None:
    async def cancelled() -> ToolResult:
        token = Token()
        executor = make_executor(
            TestTool(effects=frozenset({ToolEffect.WRITE}), target_effect=ToolEffect.WRITE),
            BlockingApproval(),
            approval_timeout=1,
        )
        task = asyncio.create_task(executor.execute(make_call(), make_context(tmp_path, token)))
        await asyncio.sleep(0)
        token.cancel()
        return await asyncio.wait_for(task, timeout=1)

    cancelled_result = asyncio.run(cancelled())
    timed_out = asyncio.run(
        make_executor(
            TestTool(effects=frozenset({ToolEffect.WRITE}), target_effect=ToolEffect.WRITE),
            BlockingApproval(),
            approval_timeout=0.01,
        ).execute(make_call(), make_context(tmp_path))
    )

    assert cancelled_result.status is ToolStatus.CANCELLED
    assert timed_out.status is ToolStatus.DENIED


def test_timeout_and_cancellation_call_abort(tmp_path: Path) -> None:
    timeout_tool = TestTool(delay=1)
    timeout_result = asyncio.run(
        make_executor(timeout_tool).execute(make_call(), make_context(tmp_path))
    )

    async def cancelled() -> tuple[ToolResult, TestTool]:
        token = Token()
        tool = TestTool(delay=1)
        task = asyncio.create_task(
            make_executor(tool).execute(make_call(), make_context(tmp_path, token))
        )
        await asyncio.sleep(0.01)
        token.cancel()
        return await asyncio.wait_for(task, timeout=1), tool

    cancelled_result, cancelled_tool = asyncio.run(cancelled())

    assert timeout_result.status is ToolStatus.TIMEOUT
    assert timeout_tool.aborts == [ToolAbortReason.TIMEOUT]
    assert cancelled_result.status is ToolStatus.CANCELLED
    assert cancelled_tool.aborts == [ToolAbortReason.CANCELLED]


def test_timeout_waits_until_abort_stops_non_cooperative_tool(tmp_path: Path) -> None:
    class AbortDrivenTool(TestTool):
        def __init__(self) -> None:
            super().__init__()
            self.release = asyncio.Event()

        async def execute(
            self,
            call: ValidatedToolCall,
            context: ExecutionContext,
            output: ToolOutputSink,
        ) -> ToolOutcome:
            self.active += 1
            try:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    await self.release.wait()
                return ToolOutcome()
            finally:
                self.active -= 1

        async def abort(
            self,
            call: ValidatedToolCall,
            context: ExecutionContext,
            reason: ToolAbortReason,
        ) -> None:
            self.aborts.append(reason)
            self.release.set()

    tool = AbortDrivenTool()
    result = asyncio.run(make_executor(tool).execute(make_call(), make_context(tmp_path)))

    assert result.status is ToolStatus.TIMEOUT
    assert tool.active == 0
    assert tool.aborts == [ToolAbortReason.TIMEOUT]


def test_tool_self_cancellation_is_wrapped_as_error(tmp_path: Path) -> None:
    class SelfCancellingTool(TestTool):
        async def execute(
            self,
            call: ValidatedToolCall,
            context: ExecutionContext,
            output: ToolOutputSink,
        ) -> ToolOutcome:
            raise asyncio.CancelledError

    result = asyncio.run(
        make_executor(SelfCancellingTool()).execute(make_call(), make_context(tmp_path))
    )

    assert result.status is ToolStatus.ERROR


def test_caller_cancellation_uses_caller_abort_reason(tmp_path: Path) -> None:
    async def run() -> list[ToolAbortReason]:
        tool = TestTool(delay=1)
        task = asyncio.create_task(make_executor(tool).execute(make_call(), make_context(tmp_path)))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return tool.aborts

    assert asyncio.run(run()) == [ToolAbortReason.CALLER_CANCELLED]


def test_invalid_tool_outcome_is_wrapped_as_error(tmp_path: Path) -> None:
    class InvalidOutcomeTool(TestTool):
        async def execute(
            self,
            call: ValidatedToolCall,
            context: ExecutionContext,
            output: ToolOutputSink,
        ) -> ToolOutcome:
            return {"invalid": True}  # type: ignore[return-value]

    result = asyncio.run(
        make_executor(InvalidOutcomeTool()).execute(make_call(), make_context(tmp_path))
    )

    assert result.status is ToolStatus.ERROR


def test_unstoppable_tool_raises_fatal_termination_error(tmp_path: Path) -> None:
    class SlowCancellationTool(TestTool):
        async def execute(
            self,
            call: ValidatedToolCall,
            context: ExecutionContext,
            output: ToolOutputSink,
        ) -> ToolOutcome:
            try:
                await asyncio.Event().wait()
                raise AssertionError("unreachable")
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)
                return ToolOutcome()

    tool = SlowCancellationTool()
    executor = ToolExecutor(
        _registry(tool),
        DefaultPermissionPolicy(),
        Approval(),
        abort_timeout_seconds=0.01,
    )

    with pytest.raises(ToolTerminationError, match="could not be terminated"):
        asyncio.run(executor.execute(make_call(), make_context(tmp_path)))


def test_unstoppable_abort_hook_raises_fatal_error(tmp_path: Path) -> None:
    class UnstoppableAbortTool(TestTool):
        async def abort(
            self,
            call: ValidatedToolCall,
            context: ExecutionContext,
            reason: ToolAbortReason,
        ) -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)

    tool = UnstoppableAbortTool(delay=1)
    executor = ToolExecutor(
        _registry(tool),
        DefaultPermissionPolicy(),
        Approval(),
        abort_timeout_seconds=0.01,
    )

    with pytest.raises(ToolTerminationError, match="abort hook"):
        asyncio.run(executor.execute(make_call(), make_context(tmp_path)))


def test_executor_sanitizes_errors_and_bounds_output(tmp_path: Path) -> None:
    failing = TestTool(fail=True)
    failure = asyncio.run(make_executor(failing).execute(make_call(), make_context(tmp_path)))
    large = TestTool(text="x" * 100_000, metadata={"large": "y" * 100_000})
    bounded = asyncio.run(make_executor(large).execute(make_call(), make_context(tmp_path)))

    assert failure.status is ToolStatus.ERROR
    assert "secret" not in failure.content
    assert bounded.status is ToolStatus.SUCCESS
    assert bounded.metadata["truncated"] is True
    assert len(bounded.content.encode()) < 100_000


def test_bounded_output_ignores_empty_writes_and_chunks_large_text() -> None:
    output = BoundedToolOutput(byte_limit=16)
    for _ in range(10_000):
        output.write("")
    output.write("你" * 10_000)

    result = output.result(ToolOutcome())

    assert result.metadata["truncated"] is True
    assert len(result.content.encode()) <= 16


def test_partial_multi_lock_acquisition_is_released_on_cancel() -> None:
    async def run() -> int:
        controller = ToolConcurrencyController(max_concurrency=3)
        holder_ready = asyncio.Event()
        release_holder = asyncio.Event()

        async def holder() -> None:
            async with controller.acquire(("b",)):
                holder_ready.set()
                await release_holder.wait()

        async def victim() -> None:
            async with controller.acquire(("a", "b")):
                raise AssertionError("victim should not acquire both locks")

        holder_task = asyncio.create_task(holder())
        await holder_ready.wait()
        victim_task = asyncio.create_task(victim())
        await asyncio.sleep(0.01)
        victim_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await victim_task
        async with asyncio.timeout(1):
            async with controller.acquire(("a",)):
                pass
        release_holder.set()
        await holder_task
        return controller.tracked_key_count

    assert asyncio.run(run()) == 0


def test_same_resource_is_serialized_when_parallelism_is_enabled(tmp_path: Path) -> None:
    async def run() -> int:
        tool = TestTool(delay=0.02, concurrency_key="workspace")
        executor = make_executor(
            tool,
            concurrency=ToolConcurrencyController(max_concurrency=2),
        )
        await asyncio.gather(
            executor.execute(make_call("call-1"), make_context(tmp_path)),
            executor.execute(make_call("call-2"), make_context(tmp_path)),
        )
        return tool.max_active

    assert asyncio.run(run()) == 1
