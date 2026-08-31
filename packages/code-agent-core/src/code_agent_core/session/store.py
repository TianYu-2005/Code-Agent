"""In-memory and file-backed tree session storage."""

import json
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .entries import (
    MessageEntryPayload,
    SessionEntry,
    SessionEntryType,
)


class SessionError(ValueError):
    """Raised when session state or persistence is invalid."""


@dataclass
class BranchInfo:
    """Summary of one branch head in the session tree."""

    head_id: str
    message_count: int
    updated_at: str


class SessionStore:
    """Append-only tree session with rewind and fork support."""

    def __init__(self) -> None:
        self._entries: dict[str, SessionEntry] = {}
        self._current_id: str | None = None
        self._children: dict[str | None, list[str]] = {}

    @property
    def current_id(self) -> str | None:
        """Identifier of the current branch head."""
        return self._current_id

    def append(self, entry: SessionEntry) -> SessionEntry:
        """Append one entry under the current head and validate tree edges."""
        if entry.id in self._entries:
            raise SessionError(f"duplicate session entry id: {entry.id}")
        if entry.parent_id is None:
            if self._current_id is not None:
                raise SessionError("session already has a root entry")
        elif entry.parent_id not in self._entries:
            raise SessionError(f"unknown parent session entry: {entry.parent_id}")

        self._entries[entry.id] = entry
        self._children.setdefault(entry.parent_id, []).append(entry.id)
        if entry.type is SessionEntryType.MESSAGE:
            self._current_id = entry.id
        return entry

    def append_message(
        self,
        message_id: str,
        payload: MessageEntryPayload,
        *,
        parent_id: str | None = None,
    ) -> SessionEntry:
        """Append a message entry, defaulting to the current head."""
        return self.append(
            SessionEntry(
                id=message_id,
                parent_id=parent_id if parent_id is not None else self._current_id,
                type=SessionEntryType.MESSAGE,
                payload=payload,
            )
        )

    def append_event(self, event_payload: object) -> SessionEntry:
        """Append a run event entry without advancing the conversation head."""
        from .entries import RunEventEntryPayload

        if not isinstance(event_payload, RunEventEntryPayload):
            raise SessionError("run event entries require a RunEventEntryPayload")
        return self.append(
            SessionEntry(
                id=f"event-{uuid.uuid4().hex[:12]}",
                parent_id=None
                if not self._entries
                else _nearest_message_ancestor(self._entries, self._current_id),
                type=SessionEntryType.RUN_EVENT,
                payload=event_payload,
            )
        )

    def current_path(self) -> tuple[SessionEntry, ...]:
        """Return message entries from the root to the current head."""
        path: list[SessionEntry] = []
        cursor = self._current_id
        while cursor is not None:
            entry = self._entries[cursor]
            if entry.type is SessionEntryType.MESSAGE:
                path.append(entry)
            cursor = entry.parent_id
        path.reverse()
        return tuple(path)

    def messages(self) -> tuple[MessageEntryPayload, ...]:
        """Return conversation messages along the current branch."""
        return tuple(
            entry.payload
            for entry in self.current_path()
            if isinstance(entry.payload, MessageEntryPayload)
        )

    def rewind(self, entry_id: str) -> None:
        """Move the current head back to a message entry on the current branch."""
        path_ids = [entry.id for entry in self.current_path()]
        if entry_id not in path_ids:
            raise SessionError(f"cannot rewind to non-branch entry: {entry_id}")
        self._current_id = entry_id

    def fork(self, entry_id: str) -> None:
        """Start a new branch from any stored message entry."""
        entry = self._entries.get(entry_id)
        if entry is None:
            raise SessionError(f"unknown session entry: {entry_id}")
        if entry.type is not SessionEntryType.MESSAGE:
            raise SessionError("forking is only supported on message entries")
        self._current_id = entry_id

    def list_branches(self) -> tuple[BranchInfo, ...]:
        """List all branch heads grouped by their message count and update time."""
        heads: dict[str, int] = {}
        child_messages = {
            parent: [
                child for child in children if self._entries[child].type is SessionEntryType.MESSAGE
            ]
            for parent, children in self._children.items()
        }
        for entry in self._entries.values():
            if entry.type is SessionEntryType.MESSAGE:
                children = child_messages.get(entry.id, [])
                if not children:
                    heads[entry.id] = 1
                else:
                    if entry.parent_id is not None:
                        heads.pop(entry.parent_id, None)
                    heads.pop(entry.id, None)
                    for child in children:
                        heads[child] = heads.get(child, 0)
        branches = []
        for head_id in heads:
            path = [
                entry
                for entry in self._entries.values()
                if entry.type is SessionEntryType.MESSAGE
                and _is_ancestor(self._entries, head_id, entry.id)
            ]
            branches.append(
                BranchInfo(
                    head_id=head_id,
                    message_count=len(path),
                    updated_at=max(entry.timestamp for entry in path).isoformat(),
                )
            )
        return tuple(sorted(branches, key=lambda branch: branch.updated_at))

    def entries(self) -> Iterator[SessionEntry]:
        """Iterate stored entries in insertion order."""
        return iter(self._entries.values())


@dataclass
class SessionFileConfig:
    """Storage location for a file-backed session."""

    path: Path


class SessionFileStore(SessionStore):
    """Tree session persisted as an append-only JSONL journal."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._load()

    def append(self, entry: SessionEntry) -> SessionEntry:
        """Append an entry and immediately persist it to disk."""
        appended = super().append(entry)
        with self._path.open("a", encoding="utf-8") as journal:
            journal.write(json.dumps(self._dump_entry(appended), ensure_ascii=False))
            journal.write("\n")
        return appended

    def _load(self) -> None:
        if not self._path.exists():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.touch()
            return
        loaded: list[SessionEntry] = []
        truncated = False
        with self._path.open("r", encoding="utf-8") as journal:
            for line in journal:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    loaded.append(SessionEntry.model_validate_json(stripped))
                except Exception:
                    truncated = True
                    break
        if truncated:
            self._rewrite(loaded)
        for entry in loaded:
            SessionStore.append(self, entry)
            if entry.type is SessionEntryType.MESSAGE:
                self._current_id = entry.id

    def _rewrite(self, entries: list[SessionEntry]) -> None:
        with self._path.open("w", encoding="utf-8") as journal:
            for entry in entries:
                journal.write(json.dumps(self._dump_entry(entry), ensure_ascii=False))
                journal.write("\n")

    @staticmethod
    def _dump_entry(entry: SessionEntry) -> dict[str, object]:
        data = entry.model_dump(mode="json")
        return {key: value for key, value in data.items() if value is not None}


def _nearest_message_ancestor(
    entries: dict[str, SessionEntry],
    head_id: str | None,
) -> str | None:
    cursor = head_id
    while cursor is not None:
        entry = entries[cursor]
        if entry.type is SessionEntryType.MESSAGE:
            return entry.id
        cursor = entry.parent_id
    return None


def _is_ancestor(
    entries: dict[str, SessionEntry],
    ancestor_id: str,
    descendant_id: str,
) -> bool:
    cursor: str | None = descendant_id
    while cursor is not None:
        if cursor == ancestor_id:
            return True
        cursor = entries[cursor].parent_id
    return False


__all__ = [
    "BranchInfo",
    "SessionError",
    "SessionFileConfig",
    "SessionFileStore",
    "SessionStore",
    "field",
]
