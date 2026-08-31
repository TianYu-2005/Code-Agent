"""Automatic context compaction for long conversations."""

import hashlib
import uuid
from dataclasses import dataclass

from code_agent_llm import (
    CancellationToken,
    Message,
    MessageRole,
    ModelEventType,
    ModelProvider,
    ModelProviderError,
    ModelRequest,
)

from ..session.entries import (
    CompactionEntryPayload,
    MessageEntryPayload,
    SessionEntry,
    SessionEntryType,
)
from ..session.store import SessionStore
from .manager import estimate_tokens

SUMMARY_INSTRUCTIONS = (
    "Summarize the earlier conversation for a coding agent. Keep, in this order: "
    "(1) the task goal, (2) changes already made (files, commands, outcomes), "
    "(3) key decisions and important file paths, (4) unfinished work. "
    "Write the summary in the conversation language. Be concise and factual."
)


@dataclass(frozen=True)
class CompactionPolicy:
    """When to trigger compaction and how much history to keep verbatim."""

    trigger_ratio: float = 0.8
    keep_recent_turns: int = 2


@dataclass(frozen=True)
class CompactionOutcome:
    """Result of one compaction check."""

    status: str  # "skipped" | "compacted" | "failed"
    messages_compacted: int = 0
    tokens_before: int = 0
    reason: str | None = None


class Compactor:
    """Summarize old messages when the current branch nears the token budget."""

    def __init__(self, provider: ModelProvider, *, policy: CompactionPolicy | None = None) -> None:
        self._provider = provider
        self._policy = policy or CompactionPolicy()

    async def maybe_compact(
        self,
        session: SessionStore,
        *,
        token_budget: int,
        model: str,
        cancellation: CancellationToken,
    ) -> CompactionOutcome:
        """Compact the session branch when the estimated tokens cross the threshold."""
        path = session.current_path()
        total = sum(
            estimate_tokens(entry.payload.message)
            for entry in path
            if isinstance(entry.payload, MessageEntryPayload)
        )
        if total <= int(token_budget * self._policy.trigger_ratio):
            return CompactionOutcome(status="skipped", tokens_before=total)

        keep_from = _retention_boundary(path, self._policy.keep_recent_turns)
        if keep_from <= 0:
            return CompactionOutcome(status="skipped", tokens_before=total)

        previous = session.latest_compaction()
        covered = 0
        prior_summary: str | None = None
        if previous is not None:
            prior_payload, covered = previous
            prior_summary = prior_payload.summary
        if keep_from <= covered:
            # The retention window overlaps the prior summary; let trim handle it.
            return CompactionOutcome(
                status="skipped",
                tokens_before=total,
                reason="retention window overlaps prior summary",
            )

        new_range = path[covered:keep_from]
        if not new_range:
            return CompactionOutcome(
                status="skipped",
                tokens_before=total,
                reason="nothing new to compact",
            )

        summary = await self._summarize(path[covered:keep_from], prior_summary, model, cancellation)
        if summary is None:
            return CompactionOutcome(
                status="failed",
                tokens_before=total,
                reason="summary request failed; falling back to trimming",
            )
        if not summary:
            return CompactionOutcome(
                status="failed",
                tokens_before=total,
                reason="summary was empty; falling back to trimming",
            )

        source_ids = tuple(entry.id for entry in path[:keep_from])
        session.append(
            SessionEntry(
                id=f"compaction-{uuid.uuid4().hex[:12]}",
                parent_id=path[keep_from - 1].id,
                type=SessionEntryType.COMPACTION,
                payload=CompactionEntryPayload(
                    summary=summary,
                    source_entry_ids=source_ids,
                    branch_head_id=path[-1].id,
                    model=model,
                    content_hash=hashlib.sha256(summary.encode("utf-8")).hexdigest(),
                ),
            )
        )
        return CompactionOutcome(
            status="compacted",
            messages_compacted=len(source_ids),
            tokens_before=total,
        )

    async def _summarize(
        self,
        entries: tuple[SessionEntry, ...],
        prior_summary: str | None,
        model: str,
        cancellation: CancellationToken,
    ) -> str | None:
        """Ask the model for a summary; return None when the request fails."""
        parts: list[str] = []
        if prior_summary:
            parts.append(f"[先前摘要]\n{prior_summary}")
        for entry in entries:
            payload = entry.payload
            if not isinstance(payload, MessageEntryPayload):
                continue
            message = payload.message
            calls = " ".join(
                f"[工具调用 {call.name}({call.arguments_json})]" for call in message.tool_calls
            )
            text = (message.content or "").strip()
            parts.append(f"[{message.role.value}] {text} {calls}".strip())
        request = ModelRequest(
            model=model,
            messages=(
                Message(
                    id="compaction-instructions",
                    role=MessageRole.SYSTEM,
                    content=SUMMARY_INSTRUCTIONS,
                ),
                Message(id="compaction-input", role=MessageRole.USER, content="\n\n".join(parts)),
            ),
        )
        streamed: list[str] = []
        final: str | None = None
        try:
            async for event in self._provider.stream(request, cancellation):
                if event.type is ModelEventType.TEXT_DELTA and event.text_delta:
                    streamed.append(event.text_delta)
                elif event.type is ModelEventType.COMPLETED and event.response is not None:
                    final = event.response.content
        except ModelProviderError:
            return None
        return (final if final is not None else "".join(streamed)).strip()


def _retention_boundary(path: tuple[SessionEntry, ...], keep_turns: int) -> int:
    """Index where the recent-turn retention window starts."""
    boundary = len(path)
    turns = 0
    for index in range(len(path) - 1, -1, -1):
        payload = path[index].payload
        if not isinstance(payload, MessageEntryPayload):
            continue
        if payload.message.role is MessageRole.USER:
            turns += 1
            boundary = index
            if turns >= keep_turns:
                return boundary
    return boundary if turns else len(path)
