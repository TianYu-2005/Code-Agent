"""Explicit registry for model provider implementations."""

from collections.abc import Iterable

from .provider import ModelProvider


class ProviderRegistryError(ValueError):
    """Raised when provider registration or lookup is invalid."""


class ProviderRegistry:
    """Register providers by stable name without import-time side effects."""

    def __init__(self, providers: Iterable[ModelProvider] = ()) -> None:
        self._providers: dict[str, ModelProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: ModelProvider) -> None:
        name = provider.info.name
        if name in self._providers:
            raise ProviderRegistryError(f"provider already registered: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> ModelProvider:
        try:
            return self._providers[name]
        except KeyError as error:
            raise ProviderRegistryError(f"unknown provider: {name}") from error

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))
