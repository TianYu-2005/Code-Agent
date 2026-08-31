"""Context construction from session branches."""

from .compaction import CompactionOutcome, CompactionPolicy, Compactor
from .manager import (
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TOKEN_BUDGET,
    ContextManager,
    ContextPolicy,
    ContextView,
    describe_context,
    estimate_tokens,
    load_project_instructions,
)

__all__ = [
    "Compactor",
    "CompactionOutcome",
    "CompactionPolicy",
    "ContextManager",
    "ContextPolicy",
    "ContextView",
    "DEFAULT_SYSTEM_PROMPT",
    "DEFAULT_TOKEN_BUDGET",
    "describe_context",
    "estimate_tokens",
    "load_project_instructions",
]
