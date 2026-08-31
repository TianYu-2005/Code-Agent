"""Deeply immutable JSON containers used at protocol boundaries."""

from collections.abc import Iterable
from math import isfinite
from typing import NoReturn, Self, SupportsIndex

MAX_JSON_DEPTH = 32


class FrozenJsonDict(dict[str, object]):
    """A JSON object that rejects mutation after construction."""

    @staticmethod
    def _immutable() -> NoReturn:
        raise TypeError("frozen JSON containers cannot be modified")

    def __setitem__(self, key: str, value: object) -> None:
        self._immutable()

    def __delitem__(self, key: str) -> None:
        self._immutable()

    def clear(self) -> None:
        self._immutable()

    def pop(self, key: str, default: object = None) -> NoReturn:
        self._immutable()

    def popitem(self) -> NoReturn:
        self._immutable()

    def setdefault(self, key: str, default: object = None) -> NoReturn:
        self._immutable()

    def update(self, *args: object, **kwargs: object) -> None:
        self._immutable()

    def __ior__(self, other: object) -> Self:  # type: ignore[misc, override]
        self._immutable()


class FrozenJsonList(list[object]):
    """A JSON array preserving list semantics while rejecting mutation."""

    @staticmethod
    def _immutable() -> NoReturn:
        raise TypeError("frozen JSON containers cannot be modified")

    def __setitem__(self, key: object, value: object) -> None:
        self._immutable()

    def __delitem__(self, key: object) -> None:
        self._immutable()

    def append(self, value: object) -> None:
        self._immutable()

    def extend(self, values: Iterable[object]) -> None:
        self._immutable()

    def insert(self, index: SupportsIndex, value: object) -> None:
        self._immutable()

    def pop(self, index: SupportsIndex = -1) -> NoReturn:
        self._immutable()

    def remove(self, value: object) -> None:
        self._immutable()

    def clear(self) -> None:
        self._immutable()

    def reverse(self) -> None:
        self._immutable()

    def sort(self, *args: object, **kwargs: object) -> None:
        self._immutable()

    def __iadd__(self, values: Iterable[object]) -> Self:  # type: ignore[misc]
        self._immutable()

    def __imul__(self, count: SupportsIndex) -> Self:
        self._immutable()


def freeze_json(value: object, *, depth: int = 0) -> object:
    """Defensively copy and recursively freeze JSON-compatible values."""
    if depth > MAX_JSON_DEPTH:
        raise ValueError(f"JSON nesting cannot exceed {MAX_JSON_DEPTH} levels")
    if isinstance(value, dict):
        return FrozenJsonDict(
            {key: freeze_json(item, depth=depth + 1) for key, item in value.items()}
        )
    if isinstance(value, list):
        return FrozenJsonList(freeze_json(item, depth=depth + 1) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze_json(item, depth=depth + 1) for item in value)
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("JSON numbers must be finite")
    return value
