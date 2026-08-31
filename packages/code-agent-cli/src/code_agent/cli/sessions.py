"""Workspace session directory management for the CLI."""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from code_agent_core.session.entries import MessageEntryPayload, SessionEntryType
from code_agent_core.session.store import SessionFileStore, SessionStore

SESSIONS_DIR = ".code-agent/sessions"
_SAFE_ID = re.compile(r"^[0-9a-zA-Z][0-9a-zA-Z._-]*$")
MAX_TITLE_CHARS = 40


@dataclass(frozen=True)
class SessionSummary:
    """One row of the session listing."""

    session_id: str
    message_count: int
    updated_at: datetime
    title: str


class SessionManagerError(ValueError):
    """Raised for invalid session identifiers or unreadable sessions."""


class SessionManager:
    """Create, list, load, and export sessions stored under the workspace."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._dir = workspace / SESSIONS_DIR

    @property
    def directory(self) -> Path:
        """Directory holding the session journals."""
        return self._dir

    def create(self) -> SessionFileStore:
        """Start a new persisted session with a timestamped identifier."""
        self._ensure_dir()
        session_id = f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
        return SessionFileStore(self._dir / f"{session_id}.jsonl")

    def load(self, session_id: str) -> SessionFileStore:
        """Load an existing session journal by identifier."""
        if not _SAFE_ID.fullmatch(session_id):
            raise SessionManagerError(f"invalid session id: {session_id!r}")
        path = self._dir / f"{session_id}.jsonl"
        if not path.is_file():
            raise SessionManagerError(f"session not found: {session_id}")
        return SessionFileStore(path)

    def list_sessions(self) -> tuple[SessionSummary, ...]:
        """List persisted sessions newest first, skipping empty ones."""
        if not self._dir.is_dir():
            return ()
        summaries: list[SessionSummary] = []
        for path in sorted(self._dir.glob("*.jsonl")):
            try:
                store = SessionFileStore(path)
            except Exception:
                continue
            summary = _summarize(store)
            if summary is not None:
                summaries.append(summary)
        summaries.sort(key=lambda item: item.updated_at, reverse=True)
        return tuple(summaries)

    def export_markdown(self, session_id: str, target: Path | None = None) -> Path:
        """Render one session as a markdown transcript."""
        store = self.load(session_id)
        target = target or (self._workspace / f"{session_id}.md")
        lines = [f"# 会话 {session_id}", ""]
        for entry in store.entries():
            if entry.type is not SessionEntryType.MESSAGE:
                continue
            payload = entry.payload
            if not isinstance(payload, MessageEntryPayload):
                continue
            message = payload.message
            role = message.role.value
            lines.append(f"## {role}")
            if message.content:
                lines.append(message.content)
            for call in message.tool_calls:
                lines.append(f"- 工具调用: `{call.name}`")
            lines.append("")
        target.write_text("\n".join(lines), encoding="utf-8")
        return target

    def remove_if_empty(self, session: SessionStore) -> None:
        """Delete the journal of a session that never received messages."""
        if not isinstance(session, SessionFileStore):
            return
        if session.current_id is not None:
            return
        session.path.unlink(missing_ok=True)

    def _ensure_dir(self) -> None:
        if self._dir.is_dir():
            return
        self._dir.mkdir(parents=True, exist_ok=True)
        gitignore = self._workspace / ".code-agent" / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("sessions/\n", encoding="utf-8")


def _summarize(store: SessionFileStore) -> SessionSummary | None:
    messages = store.messages()
    if not messages:
        return None
    title = next(
        (payload.message.content for payload in messages if payload.message.role.value == "user"),
        messages[0].message.content,
    )
    flat = " ".join(title.split())
    if len(flat) > MAX_TITLE_CHARS:
        flat = f"{flat[:MAX_TITLE_CHARS]}…"
    updated_at = max(entry.timestamp for entry in store.entries())
    return SessionSummary(
        session_id=store.session_id,
        message_count=len(messages),
        updated_at=updated_at,
        title=flat or "(空消息)",
    )
