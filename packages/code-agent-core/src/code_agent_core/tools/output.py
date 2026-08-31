"""Bounded output collection for tool executions."""

from collections.abc import Mapping, Sequence

from pydantic import JsonValue, ValidationError

from .base import MAX_TOOL_RESULT_BYTES, ToolOutcome, ToolResult, ToolStatus

_METADATA_BUDGET = 8_192
_METADATA_DEPTH = 16
_METADATA_ITEMS = 256


class BoundedToolOutput:
    """Collect UTF-8 text incrementally without retaining overflow."""

    def __init__(self, byte_limit: int = MAX_TOOL_RESULT_BYTES // 2) -> None:
        if byte_limit < 1:
            raise ValueError("byte_limit must be positive")
        self._byte_limit = byte_limit
        self._parts: list[str] = []
        self._kept_bytes = 0
        self._original_bytes = 0
        self._truncated = False

    def write(self, text: str) -> None:
        if not text:
            return
        for offset in range(0, len(text), 4_096):
            encoded = text[offset : offset + 4_096].encode("utf-8", errors="replace")
            self._original_bytes += len(encoded)
            remaining = self._byte_limit - self._kept_bytes
            if remaining <= 0:
                self._truncated = True
                continue
            kept = encoded[:remaining]
            if len(kept) < len(encoded):
                self._truncated = True
            decoded = kept.decode("utf-8", errors="ignore")
            if decoded:
                self._parts.append(decoded)
                self._kept_bytes += len(decoded.encode("utf-8"))

    def result(self, outcome: ToolOutcome) -> ToolResult:
        metadata, metadata_truncated = _bounded_metadata(outcome.metadata)
        if self._truncated:
            metadata.update(
                {
                    "truncated": True,
                    "original_size_bytes": self._original_bytes,
                }
            )
        if metadata_truncated:
            metadata["metadata_truncated"] = True
        content = "".join(self._parts)
        try:
            return ToolResult(
                status=ToolStatus.SUCCESS,
                content=content,
                metadata=metadata,
            )
        except ValidationError:
            return ToolResult(
                status=ToolStatus.SUCCESS,
                content=content[:1_024],
                metadata={
                    "truncated": True,
                    "original_size_bytes": self._original_bytes,
                    "metadata_truncated": True,
                },
            )


def _bounded_metadata(
    metadata: Mapping[str, JsonValue],
) -> tuple[dict[str, JsonValue], bool]:
    """Copy metadata only after an allocation-bounded structural estimate."""
    budget = [_METADATA_BUDGET]
    items = [0]
    if not _fits_json(metadata, budget=budget, items=items, depth=0):
        return {}, True
    return dict(metadata), False


def _fits_json(value: object, *, budget: list[int], items: list[int], depth: int) -> bool:
    if depth > _METADATA_DEPTH or items[0] > _METADATA_ITEMS:
        return False
    items[0] += 1
    if isinstance(value, str):
        budget[0] -= len(value.encode("utf-8", errors="replace"))
        return budget[0] >= 0
    if value is None or isinstance(value, bool | int | float):
        budget[0] -= 32
        return budget[0] >= 0
    if isinstance(value, Mapping):
        if len(value) > _METADATA_ITEMS:
            return False
        for key, item in value.items():
            if not isinstance(key, str) or not _fits_json(
                key, budget=budget, items=items, depth=depth + 1
            ):
                return False
            if not _fits_json(item, budget=budget, items=items, depth=depth + 1):
                return False
        return True
    if isinstance(value, Sequence):
        if len(value) > _METADATA_ITEMS:
            return False
        return all(_fits_json(item, budget=budget, items=items, depth=depth + 1) for item in value)
    return False
