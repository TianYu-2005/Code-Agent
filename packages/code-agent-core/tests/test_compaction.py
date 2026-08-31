"""Tests for automatic context compaction."""

import asyncio
import hashlib

from code_agent_core.context import CompactionPolicy, Compactor, ContextManager, ContextPolicy
from code_agent_core.context.compaction import SUMMARY_INSTRUCTIONS, _retention_boundary
from code_agent_core.runtime.loop import AgentLoop, LoopEndReason
from code_agent_core.session import (
    MessageEntryPayload,
    SessionEntry,
    SessionEntryType,
    SessionStore,
)
from code_agent_llm import (
    FakeProvider,
    FinishReason,
    Message,
    MessageRole,
    ModelEvent,
    ModelEventType,
    ModelProviderError,
    ModelResponse,
    ProviderErrorCode,
    ToolCall,
)


class _NoCancel:
    """Cancellation token stub that never fires."""

    @property
    def is_cancelled(self) -> bool:
        return False

    async def wait(self) -> None:
        await asyncio.Event().wait()


def _append(
    session: SessionStore,
    entry_id: str,
    role: MessageRole,
    content: str,
    *,
    tool_calls: tuple[ToolCall, ...] = (),
    tool_call_id: str | None = None,
) -> None:
    session.append(
        SessionEntry(
            id=entry_id,
            parent_id=None if len(session.current_path()) == 0 else session.current_path()[-1].id,
            type=SessionEntryType.MESSAGE,
            payload=MessageEntryPayload(
                message=Message(
                    id=entry_id,
                    role=role,
                    content=content,
                    tool_calls=tool_calls,
                    tool_call_id=tool_call_id,
                )
            ),
        )
    )


def _summary_response(text: str) -> list[ModelEvent]:
    return [
        ModelEvent(
            type=ModelEventType.COMPLETED,
            response=ModelResponse(content=text, finish_reason=FinishReason.STOP),
        )
    ]


def _long_history(session: SessionStore) -> None:
    _append(session, "m1", MessageRole.USER, "整理项目结构 " + "细节" * 200)
    _append(session, "m2", MessageRole.ASSISTANT, "我先看一下目录 " + "分析" * 200)
    _append(session, "m3", MessageRole.USER, "然后修复登录 bug " + "上下文" * 200)
    _append(session, "m4", MessageRole.ASSISTANT, "已修复 " + "说明" * 200)
    _append(session, "m5", MessageRole.USER, "现在跑一下测试")


def test_compaction_skipped_below_threshold() -> None:
    session = SessionStore()
    _append(session, "m1", MessageRole.USER, "hello")
    _append(session, "m2", MessageRole.ASSISTANT, "hi")
    provider = FakeProvider([_summary_response("unused")])
    compactor = Compactor(provider)

    outcome = asyncio.run(
        compactor.maybe_compact(
            session, token_budget=32_000, model="test-model", cancellation=_NoCancel()
        )
    )

    assert outcome.status == "skipped"
    assert session.latest_compaction() is None
    assert provider.requests == []


def test_compaction_summarizes_old_messages() -> None:
    session = SessionStore()
    _long_history(session)
    provider = FakeProvider([_summary_response("任务：整理并修复登录。")])
    compactor = Compactor(provider)

    outcome = asyncio.run(
        compactor.maybe_compact(
            session, token_budget=400, model="test-model", cancellation=_NoCancel()
        )
    )

    assert outcome.status == "compacted"
    assert outcome.messages_compacted == 2  # m1, m2 summarized; m3.. kept verbatim

    compaction = session.latest_compaction()
    assert compaction is not None
    payload, covered = compaction
    assert payload.summary == "任务：整理并修复登录。"
    assert payload.source_entry_ids == ("m1", "m2")
    assert payload.branch_head_id == "m5"
    assert payload.model == "test-model"
    assert payload.content_hash == hashlib.sha256("任务：整理并修复登录。".encode()).hexdigest()
    assert covered == 2
    # The summary request carried the old messages, not the recent ones.
    request = provider.requests[0]
    assert request.messages[0].content == SUMMARY_INSTRUCTIONS
    assert "整理项目结构" in request.messages[1].content
    assert "现在跑一下测试" not in request.messages[1].content

    # Context build now injects the summary instead of the compacted messages.
    manager = ContextManager(session, policy=ContextPolicy(token_budget=400))
    built = manager.build("test-model")
    contents = [message.content or "" for message in built.messages]
    assert any("任务：整理并修复登录。" in content for content in contents)
    assert not any("整理项目结构" in content for content in contents)
    assert any("现在跑一下测试" in content for content in contents)


def test_retention_boundary_keeps_tool_pairing() -> None:
    session = SessionStore()
    _append(session, "m1", MessageRole.USER, "读取配置文件 " + "细节" * 100)
    _append(
        session,
        "m2",
        MessageRole.ASSISTANT,
        "我来读取 " + "说明" * 100,
        tool_calls=(ToolCall(id="call-1", name="read_file", arguments_json='{"path":"cfg"}'),),
    )
    _append(session, "m3", MessageRole.TOOL, "配置内容", tool_call_id="call-1")
    _append(session, "m4", MessageRole.USER, "很好，继续")
    _append(session, "m5", MessageRole.ASSISTANT, "好的")
    provider = FakeProvider([_summary_response("摘要")])
    compactor = Compactor(provider, policy=CompactionPolicy(keep_recent_turns=1))

    outcome = asyncio.run(
        compactor.maybe_compact(
            session, token_budget=100, model="test-model", cancellation=_NoCancel()
        )
    )

    assert outcome.status == "compacted"
    compaction = session.latest_compaction()
    assert compaction is not None
    # The compacted range ends after the complete tool pairing.
    assert compaction[0].source_entry_ids == ("m1", "m2", "m3")


def test_incremental_compaction_includes_prior_summary() -> None:
    session = SessionStore()
    _append(session, "m1", MessageRole.USER, "第一问 " + "细节" * 400)
    _append(session, "m2", MessageRole.ASSISTANT, "第一答 " + "说明" * 400)
    _append(session, "m3", MessageRole.USER, "第二问 " + "细节" * 400)
    _append(session, "m4", MessageRole.ASSISTANT, "第二答")
    provider = FakeProvider(
        [
            _summary_response("第一次摘要：任务一是……"),
            _summary_response("第二次摘要：涵盖全部"),
        ]
    )
    compactor = Compactor(provider, policy=CompactionPolicy(keep_recent_turns=1))

    first = asyncio.run(
        compactor.maybe_compact(
            session, token_budget=400, model="test-model", cancellation=_NoCancel()
        )
    )
    assert first.status == "compacted"

    # Continue the conversation well past the threshold again.
    _append(session, "m5", MessageRole.USER, "第三问 " + "细节" * 400)
    _append(session, "m6", MessageRole.ASSISTANT, "第三答")

    second = asyncio.run(
        compactor.maybe_compact(
            session, token_budget=400, model="test-model", cancellation=_NoCancel()
        )
    )
    assert second.status == "compacted"

    # The second summary request must include the first summary text.
    second_request = provider.requests[1]
    assert "第一次摘要" in second_request.messages[1].content
    assert "第二问" in second_request.messages[1].content

    compaction = session.latest_compaction()
    assert compaction is not None
    assert compaction[0].summary == "第二次摘要：涵盖全部"
    assert compaction[0].source_entry_ids == ("m1", "m2", "m3", "m4")
    assert compaction[1] == 4


def test_compaction_failure_degrades_to_trim() -> None:
    session = SessionStore()
    _long_history(session)
    provider = FakeProvider([ModelProviderError(ProviderErrorCode.CONNECTION, "network down")])
    compactor = Compactor(provider)

    outcome = asyncio.run(
        compactor.maybe_compact(
            session, token_budget=400, model="test-model", cancellation=_NoCancel()
        )
    )

    assert outcome.status == "failed"
    assert outcome.reason is not None
    assert session.latest_compaction() is None

    # Building still works; the trim fallback drops the oldest messages and
    # realigns the window to start at a user message.
    manager = ContextManager(session, policy=ContextPolicy(token_budget=400))
    built = manager.build("test-model")
    roles = [message.role for message in built.messages]
    history_roles = [role for role in roles if role is not MessageRole.SYSTEM]
    assert history_roles[0] is MessageRole.USER


def test_trim_realigns_to_user_boundary() -> None:
    session = SessionStore()
    _append(session, "m1", MessageRole.USER, "第一问 " + "细节" * 100)
    _append(session, "m2", MessageRole.ASSISTANT, "第一答 " + "说明" * 100)
    _append(session, "m3", MessageRole.USER, "第二问 " + "细节" * 100)
    _append(session, "m4", MessageRole.ASSISTANT, "第二答 " + "说明" * 100)
    manager = ContextManager(session, policy=ContextPolicy(token_budget=130))

    built = manager.build("test-model")

    history = [message for message in built.messages if message.role is not MessageRole.SYSTEM]
    assert history[0].role is MessageRole.USER
    assert len(history) == 2


def test_loop_emits_context_compacted_event() -> None:
    from code_agent_core import (
        DefaultPermissionPolicy,
        RunBudgets,
        RunSpec,
        RuntimeEvent,
        RuntimeEventType,
        ToolExecutor,
        ToolRegistry,
    )

    class _Sink:
        def __init__(self) -> None:
            self.events: list[RuntimeEvent] = []

        async def emit(self, event: RuntimeEvent) -> None:
            self.events.append(event)

    session = SessionStore()
    _append(session, "h1", MessageRole.USER, "首个历史问题 " + "细节" * 400)
    _append(session, "h2", MessageRole.ASSISTANT, "首个历史回答 " + "说明" * 400)
    _append(session, "h3", MessageRole.USER, "后来历史问题 " + "细节" * 400)
    _append(session, "h4", MessageRole.ASSISTANT, "后来历史回答 " + "说明" * 400)

    provider = FakeProvider(
        [
            _summary_response("会话摘要"),
            _summary_response("最终回答"),
        ]
    )
    registry = ToolRegistry()
    executor = ToolExecutor(registry, DefaultPermissionPolicy(), None)  # type: ignore[arg-type]
    sink = _Sink()
    loop = AgentLoop(
        provider=provider,
        session=session,
        context_manager=ContextManager(session, policy=ContextPolicy(token_budget=500)),
        tool_registry=registry,
        tool_executor=executor,
        run_spec=RunSpec(
            session_id="session-1",
            model="test-model",
            budgets=RunBudgets(max_turns=5),
        ),
        event_sink=sink,
        compactor=Compactor(provider),
    )

    result = asyncio.run(
        loop.run(Message(id="m5", role=MessageRole.USER, content="现在跑一下测试"))
    )

    assert result.end_reason is LoopEndReason.COMPLETED
    compacted = [event for event in sink.events if event.type is RuntimeEventType.CONTEXT_COMPACTED]
    assert len(compacted) == 1
    assert compacted[0].payload["status"] == "compacted"
    assert compacted[0].payload["messages_compacted"] == 2
    assert compacted[0].turn_id is not None

    # The final model request contains the summary instead of the old history.
    final_request = provider.requests[-1]
    contents = [message.content or "" for message in final_request.messages]
    assert any("会话摘要" in content for content in contents)
    assert not any("首个历史问题" in content for content in contents)
    assert any("后来历史问题" in content for content in contents)
    assert any("现在跑一下测试" in content for content in contents)


def test_retention_boundary_helper_without_user_messages() -> None:
    session = SessionStore()
    _append(session, "m1", MessageRole.ASSISTANT, "只有助手消息")

    path = session.current_path()
    assert _retention_boundary(path, 2) == len(path)
