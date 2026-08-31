"""Context construction from session branches."""

from dataclasses import dataclass
from pathlib import Path

from code_agent_llm import Message, MessageRole, ModelRequest, ModelToolSpec, ProtocolModel

from ..session.store import SessionStore

DEFAULT_SYSTEM_PROMPT = (
    "You are a terminal coding agent. Explore the workspace before editing, "
    "verify changes with available tools, and summarize what you changed."
)
DEFAULT_TOKEN_BUDGET = 32_000


def estimate_tokens(message: Message) -> int:
    """Estimate token usage for one message using a simple heuristic."""
    total = len(message.content) // 4
    total += sum(len(call.arguments_json) // 4 for call in message.tool_calls)
    return max(total, 1)


@dataclass(frozen=True)
class ContextPolicy:
    """Configuration for building model requests from a session."""

    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    project_instructions: str | None = None
    token_budget: int = DEFAULT_TOKEN_BUDGET


class ContextManager:
    """Build model requests from the current session branch."""

    def __init__(
        self,
        session: SessionStore,
        *,
        policy: ContextPolicy | None = None,
    ) -> None:
        self._session = session
        self._policy = policy or ContextPolicy()

    @property
    def policy(self) -> ContextPolicy:
        """Return the active context construction policy."""
        return self._policy

    def build(
        self,
        model: str,
        extra_messages: tuple[Message, ...] = (),
        tools: tuple[ModelToolSpec, ...] = (),
    ) -> ModelRequest:
        """Assemble a model request without mutating the session."""
        messages: list[Message] = []
        messages.append(
            Message(
                id="system-instructions",
                role=MessageRole.SYSTEM,
                content=self._policy.system_prompt,
            )
        )
        if self._policy.project_instructions:
            messages.append(
                Message(
                    id="system-project",
                    role=MessageRole.SYSTEM,
                    content=self._policy.project_instructions,
                )
            )
        history = [entry.message for entry in self._session.messages()]
        history.extend(extra_messages)
        messages.extend(self._trim(history))
        return ModelRequest(messages=tuple(messages), model=model, tools=tools)

    def _trim(self, messages: list[Message]) -> tuple[Message, ...]:
        """Keep the most recent messages within the configured token budget."""
        if not messages:
            return ()
        costs = [estimate_tokens(message) for message in messages]
        if sum(costs) <= self._policy.token_budget:
            return tuple(messages)
        kept: list[Message] = []
        total = 0
        for message, cost in reversed(list(zip(messages, costs, strict=True))):
            if total + cost > self._policy.token_budget and kept:
                break
            kept.append(message)
            total += cost
        kept.reverse()
        return tuple(kept)


def load_project_instructions(workspace: Path) -> str | None:
    """Load AGENTS.md from the workspace root if present."""
    instructions_path = workspace / "AGENTS.md"
    if not instructions_path.is_file():
        return None
    try:
        return instructions_path.read_text(encoding="utf-8")
    except OSError:
        return None


class ContextView(ProtocolModel):
    """Snapshot of context construction for debugging."""

    total_messages: int
    included_messages: int
    estimated_tokens: int
    truncated: bool


def describe_context(context_manager: ContextManager) -> ContextView:
    """Summarize what the next request would include."""
    history = [entry.message for entry in context_manager._session.messages()]
    included = context_manager._trim(history)
    included_tokens = sum(estimate_tokens(message) for message in included)
    return ContextView(
        total_messages=len(history),
        included_messages=len(included),
        estimated_tokens=included_tokens,
        truncated=len(included) < len(history),
    )


__all__ = [
    "ContextManager",
    "ContextPolicy",
    "ContextView",
    "DEFAULT_SYSTEM_PROMPT",
    "DEFAULT_TOKEN_BUDGET",
    "describe_context",
    "estimate_tokens",
    "load_project_instructions",
]
