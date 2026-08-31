import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from code_agent_llm import (
    FakeProvider,
    FinishReason,
    Message,
    MessageRole,
    ModelCapability,
    ModelEvent,
    ModelEventType,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
    ModelResponse,
    NeverCancelToken,
    ProviderErrorCode,
    ProviderInfo,
    ProviderRegistry,
    ProviderRegistryError,
    RetryingProvider,
    RetryPolicy,
)
from code_agent_llm.provider import CancellationToken


def request() -> ModelRequest:
    return ModelRequest(
        messages=(Message(id="message-1", role=MessageRole.USER, content="hello"),),
        model="test-model",
    )


async def collect(
    provider: ModelProvider,
    cancellation: CancellationToken,
) -> list[ModelEvent]:
    return [event async for event in provider.stream(request(), cancellation)]


def test_fake_provider_replays_responses_and_records_requests() -> None:
    response = ModelResponse(content="done", finish_reason=FinishReason.STOP)
    provider = FakeProvider.from_responses((response,))

    events = asyncio.run(collect(provider, NeverCancelToken()))

    assert events == [ModelEvent(type=ModelEventType.COMPLETED, response=response)]
    assert provider.requests == [request()]
    assert ModelCapability.STREAMING in provider.info.capabilities


def test_fake_provider_rejects_exhausted_script() -> None:
    provider = FakeProvider(())

    with pytest.raises(ModelProviderError) as raised:
        asyncio.run(collect(provider, NeverCancelToken()))

    assert raised.value.code is ProviderErrorCode.INVALID_REQUEST


@dataclass
class CancelledToken:
    is_cancelled: bool = True

    async def wait(self) -> None:
        return None


def test_fake_provider_honors_cancellation() -> None:
    provider = FakeProvider.from_responses(
        (ModelResponse(content="done", finish_reason=FinishReason.STOP),)
    )

    with pytest.raises(ModelProviderError) as raised:
        asyncio.run(collect(provider, CancelledToken()))

    assert raised.value.code is ProviderErrorCode.CANCELLED
    assert provider.requests == []


class FlakyProvider:
    def __init__(self, failures: int) -> None:
        self.info = ProviderInfo(name="flaky", capabilities=frozenset())
        self.failures = failures
        self.calls = 0

    async def stream(
        self,
        model_request: ModelRequest,
        cancellation: CancellationToken,
    ) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        if self.calls <= self.failures:
            raise ModelProviderError(
                ProviderErrorCode.CONNECTION,
                "temporary failure",
                retryable=True,
            )
        yield ModelEvent(
            type=ModelEventType.COMPLETED,
            response=ModelResponse(content="done", finish_reason=FinishReason.STOP),
        )


def test_retrying_provider_retries_transient_pre_stream_failure() -> None:
    provider = FlakyProvider(failures=2)
    delays: list[float] = []

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    retrying = RetryingProvider(
        provider,
        RetryPolicy(max_attempts=3, initial_delay_seconds=0.1, multiplier=2),
        sleep=record_sleep,
    )

    events = asyncio.run(collect(retrying, NeverCancelToken()))

    assert events[-1].type is ModelEventType.COMPLETED
    assert provider.calls == 3
    assert delays == [0.1, 0.2]


def test_retrying_provider_does_not_retry_after_output() -> None:
    class PartialProvider(FlakyProvider):
        async def stream(
            self,
            model_request: ModelRequest,
            cancellation: CancellationToken,
        ) -> AsyncIterator[ModelEvent]:
            self.calls += 1
            yield ModelEvent(type=ModelEventType.TEXT_DELTA, text_delta="partial")
            raise ModelProviderError(
                ProviderErrorCode.CONNECTION,
                "stream failed",
                retryable=True,
            )

    provider = PartialProvider(failures=0)
    retrying = RetryingProvider(provider, RetryPolicy(max_attempts=3))

    with pytest.raises(ModelProviderError):
        asyncio.run(collect(retrying, NeverCancelToken()))

    assert provider.calls == 1


def test_provider_registry_rejects_duplicates_and_unknown_names() -> None:
    provider = FakeProvider(())
    registry = ProviderRegistry((provider,))

    assert registry.get("fake") is provider
    assert registry.names() == ("fake",)
    with pytest.raises(ProviderRegistryError, match="already registered"):
        registry.register(provider)
    with pytest.raises(ProviderRegistryError, match="unknown provider"):
        registry.get("missing")
