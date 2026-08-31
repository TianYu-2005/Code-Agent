"""Model provider contracts and reusable provider behavior."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import Field

from .types import ModelEvent, ModelRequest, ProtocolModel


@runtime_checkable
class CancellationToken(Protocol):
    """Cooperative cancellation signal accepted by model providers."""

    @property
    def is_cancelled(self) -> bool:
        """Return whether cancellation has been requested."""
        ...

    async def wait(self) -> None:
        """Wait until cancellation is requested."""
        ...


@dataclass(frozen=True, slots=True)
class NeverCancelToken:
    """Cancellation token used when a caller has no cancellation source."""

    @property
    def is_cancelled(self) -> bool:
        return False

    async def wait(self) -> None:
        await asyncio.Event().wait()


async def await_with_cancellation[T](
    operation: Awaitable[T],
    cancellation: CancellationToken,
) -> T:
    """Await an operation while reacting immediately to cooperative cancellation."""
    if cancellation.is_cancelled:
        if asyncio.iscoroutine(operation):
            operation.close()
        raise ModelProviderError(
            ProviderErrorCode.CANCELLED,
            "model request was cancelled",
        )
    operation_task = asyncio.ensure_future(operation)
    cancellation_task = asyncio.create_task(cancellation.wait())
    try:
        done, _ = await asyncio.wait(
            {operation_task, cancellation_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if operation_task in done:
            return await operation_task
        raise ModelProviderError(
            ProviderErrorCode.CANCELLED,
            "model request was cancelled",
        )
    finally:
        for task in (operation_task, cancellation_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(operation_task, cancellation_task, return_exceptions=True)


class ModelCapability(StrEnum):
    """Optional capabilities exposed by a model provider."""

    STREAMING = "streaming"
    TOOL_CALLING = "tool_calling"
    USAGE = "usage"


class ProviderInfo(ProtocolModel):
    """Stable metadata used to select and inspect a provider."""

    name: str = Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    capabilities: frozenset[ModelCapability] = frozenset()


@runtime_checkable
class ModelProvider(Protocol):
    """Provider-independent asynchronous streaming model API."""

    @property
    def info(self) -> ProviderInfo:
        """Return provider metadata."""
        ...

    def stream(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> AsyncIterator[ModelEvent]:
        """Stream normalized events for one request."""
        ...


class ProviderErrorCode(StrEnum):
    """Normalized model provider failure categories."""

    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    INVALID_REQUEST = "invalid_request"
    MALFORMED_RESPONSE = "malformed_response"
    CANCELLED = "cancelled"
    SERVER = "server"
    UNKNOWN = "unknown"


class ModelProviderError(Exception):
    """Normalized provider error safe for runtime policy decisions."""

    def __init__(
        self,
        code: ProviderErrorCode,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True, slots=True, kw_only=True)
class RetryPolicy:
    """Bounded retry policy for transient failures before stream output."""

    max_attempts: int = 3
    initial_delay_seconds: float = 0.25
    max_delay_seconds: float = 2.0
    multiplier: float = 2.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.initial_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("retry delays cannot be negative")
        if self.multiplier < 1:
            raise ValueError("retry multiplier cannot be smaller than one")


Sleep = Callable[[float], Awaitable[None]]


class RetryingProvider:
    """Retry transient provider failures that occur before the first event."""

    def __init__(
        self,
        provider: ModelProvider,
        policy: RetryPolicy,
        *,
        sleep: Sleep | None = None,
    ) -> None:
        self._provider = provider
        self._policy = policy
        self._sleep = sleep or asyncio.sleep

    @property
    def info(self) -> ProviderInfo:
        return self._provider.info

    async def stream(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> AsyncIterator[ModelEvent]:
        delay = self._policy.initial_delay_seconds
        for attempt in range(1, self._policy.max_attempts + 1):
            emitted = False
            try:
                async for event in self._provider.stream(request, cancellation):
                    emitted = True
                    yield event
                return
            except ModelProviderError as error:
                final_attempt = attempt == self._policy.max_attempts
                if emitted or not error.retryable or final_attempt:
                    raise
                await await_with_cancellation(
                    self._sleep(min(delay, self._policy.max_delay_seconds)),
                    cancellation,
                )
                delay *= self._policy.multiplier
