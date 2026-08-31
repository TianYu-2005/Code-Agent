"""Deterministic model provider for offline tests."""

from collections.abc import AsyncIterator, Iterable

from .provider import (
    CancellationToken,
    ModelCapability,
    ModelProviderError,
    ProviderErrorCode,
    ProviderInfo,
)
from .types import ModelEvent, ModelEventType, ModelRequest, ModelResponse


class FakeProvider:
    """Replay scripted event streams and record requests."""

    def __init__(
        self,
        scripts: Iterable[Iterable[ModelEvent] | Exception],
        *,
        name: str = "fake",
    ) -> None:
        self._scripts = [
            script if isinstance(script, Exception) else tuple(script) for script in scripts
        ]
        self._next_script = 0
        self.requests: list[ModelRequest] = []
        self.info = ProviderInfo(
            name=name,
            capabilities=frozenset(ModelCapability),
        )

    @classmethod
    def from_responses(
        cls,
        responses: Iterable[ModelResponse],
        *,
        name: str = "fake",
    ) -> "FakeProvider":
        return cls(
            (
                (ModelEvent(type=ModelEventType.COMPLETED, response=response),)
                for response in responses
            ),
            name=name,
        )

    async def stream(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> AsyncIterator[ModelEvent]:
        if cancellation.is_cancelled:
            raise ModelProviderError(
                ProviderErrorCode.CANCELLED,
                "model request was cancelled",
            )
        if self._next_script >= len(self._scripts):
            raise ModelProviderError(
                ProviderErrorCode.INVALID_REQUEST,
                "fake provider has no remaining script",
            )

        script = self._scripts[self._next_script]
        self._next_script += 1
        self.requests.append(request)
        if isinstance(script, Exception):
            raise script

        for event in script:
            if cancellation.is_cancelled:
                raise ModelProviderError(
                    ProviderErrorCode.CANCELLED,
                    "model request was cancelled",
                )
            yield event
