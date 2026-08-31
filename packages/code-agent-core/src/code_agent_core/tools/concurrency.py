"""Concurrency controls for tool invocations."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass


@dataclass(slots=True)
class _LockEntry:
    lock: asyncio.Lock
    users: int = 0


class ToolConcurrencyController:
    """Apply a global limit and deterministic keyed mutual exclusion."""

    def __init__(self, max_concurrency: int = 1) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least one")
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._locks: dict[str, _LockEntry] = {}
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, keys: tuple[str, ...]) -> AsyncIterator[None]:
        unique_keys = tuple(sorted(set(keys)))
        entries: list[_LockEntry] = []
        acquired: list[_LockEntry] = []
        await self._semaphore.acquire()
        try:
            async with self._guard:
                for key in unique_keys:
                    entry = self._locks.setdefault(key, _LockEntry(asyncio.Lock()))
                    entry.users += 1
                    entries.append(entry)
            try:
                for entry in entries:
                    await entry.lock.acquire()
                    acquired.append(entry)
                yield
            finally:
                for entry in reversed(acquired):
                    entry.lock.release()
                async with self._guard:
                    for key, entry in zip(unique_keys, entries, strict=True):
                        entry.users -= 1
                        if entry.users == 0 and not entry.lock.locked():
                            self._locks.pop(key, None)
        finally:
            self._semaphore.release()

    @property
    def tracked_key_count(self) -> int:
        """Expose lock-table size for diagnostics and leak tests."""
        return len(self._locks)
