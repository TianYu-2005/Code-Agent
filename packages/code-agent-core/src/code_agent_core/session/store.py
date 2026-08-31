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
        """List all branch heads with their message counts and update times."""
        parents_of_messages = {
            parent
            for parent, children in self._children.items()
            if any(self._entries[child].type is SessionEntryType.MESSAGE for child in children)
        }
        branches = []
        for entry_id, entry in self._entries.items():
            if entry.type is not SessionEntryType.MESSAGE or entry_id in parents_of_messages:
                continue
            count = 0
            cursor: str | None = entry_id
            while cursor is not None:
                node = self._entries[cursor]
                if node.type is SessionEntryType.MESSAGE:
                    count += 1
                cursor = node.parent_id
            branches.append(
                BranchInfo(
                    head_id=entry_id,
                    message_count=count,
                    updated_at=entry.timestamp.isoformat(),
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

    @property
    def session_id(self) -> str:
        """Stable identifier derived from the journal filename."""
        return self._path.stem

    @property
    def path(self) -> Path:
        """Location of the JSONL journal backing this session."""
        return self._path

    def append(self, entry: SessionEntry) -> SessionEntry:
        """Append an entry and immediately persist it to disk."""
        appended = super().append(entry)
        with self._path.open("a", encoding="utf-8") as journal:
            journal.write(json.dumps(self._dump_entry(appended), ensure_ascii=False))
            journal.write("\n")
        self._write_head()
        return appended

    def rewind(self, entry_id: str) -> None:
        """Move the head back and persist the new position."""
        super().rewind(entry_id)
        self._write_head()

    def fork(self, entry_id: str) -> None:
        """Move the head to a branch point and persist the new position."""
        super().fork(entry_id)
        self._write_head()

    def _head_path(self) -> Path:
        return self._path.with_suffix(".head")

    def _write_head(self) -> None:
        head = self._head_path()
        if self._current_id is None:
            head.unlink(missing_ok=True)
            return
        head.write_text(f"{self._current_id}\n", encoding="utf-8")

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
        self._restore_head()

    def _restore_head(self) -> None:
        head = self._head_path()
        if not head.exists():
            return
        head_id = head.read_text(encoding="utf-8").strip()
        entry = self._entries.get(head_id)
        if entry is not None and entry.type is SessionEntryType.MESSAGE:
            self._current_id = head_id

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


__all__ = [
    "BranchInfo",
    "SessionError",
    "SessionFileConfig",
    "SessionFileStore",
    "SessionStore",
    "field",
]
