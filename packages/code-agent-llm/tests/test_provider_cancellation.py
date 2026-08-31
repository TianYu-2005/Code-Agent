import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field

from code_agent_llm import (
    Message,
    MessageRole,
    ModelEvent,
    ModelProviderError,
    ModelRequest,
    ProviderErrorCode,
    ProviderInfo,
    RetryingProvider,
    RetryPolicy,
)
from code_agent_llm.provider import CancellationToken


@dataclass
class EventCancellation:
    event: asyncio.Event = field(default_factory=asyncio.Event)

    @property
    def is_cancelled(self) -> bool:
        return self.event.is_set()

    async def wait(self) -> None:
        await self.event.wait()

    def cancel(self) -> None:
        self.event.set()


def request() -> ModelRequest:
    return ModelRequest(
        messages=(Message(id="message-1", role=MessageRole.USER, content="hello"),),
        model="test-model",
    )


class AlwaysFailingProvider:
    info = ProviderInfo(name="failing")

    async def stream(
        self,
        model_request: ModelRequest,
        cancellation: CancellationToken,
    ) -> AsyncIterator[ModelEvent]:
        raise ModelProviderError(
            ProviderErrorCode.CONNECTION,
            "temporary failure",
            retryable=True,
        )
        yield


def test_retry_backoff_is_immediately_cancellable() -> None:
    async def scenario() -> ProviderErrorCode:
        cancellation = EventCancellation()
        sleep_started = asyncio.Event()

        async def blocked_sleep(delay: float) -> None:
            sleep_started.set()
            await asyncio.Event().wait()

        provider = RetryingProvider(
            AlwaysFailingProvider(),
            RetryPolicy(max_attempts=3),
            sleep=blocked_sleep,
        )

        async def consume() -> None:
            async for _ in provider.stream(request(), cancellation):
                pass

        task = asyncio.create_task(consume())
        await asyncio.wait_for(sleep_started.wait(), timeout=1)
        cancellation.cancel()
        try:
            await asyncio.wait_for(task, timeout=1)
        except ModelProviderError as error:
            return error.code
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError, ModelProviderError):
                await task
        raise AssertionError("retry was not cancelled")

    assert asyncio.run(scenario()) is ProviderErrorCode.CANCELLED
