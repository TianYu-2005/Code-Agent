"""Model provider boundary for Code Agent."""

from .types import (
    FinishReason,
    GenerationConfig,
    Message,
    MessageRole,
    ModelEvent,
    ModelEventType,
    ModelRequest,
    ModelResponse,
    ModelToolSpec,
    ProtocolModel,
    TokenUsage,
    ToolCall,
    ToolCallDelta,
)

__version__ = "0.1.0"

__all__ = [
    "FinishReason",
    "GenerationConfig",
    "Message",
    "MessageRole",
    "ModelEvent",
    "ModelEventType",
    "ModelRequest",
    "ModelResponse",
    "ModelToolSpec",
    "ProtocolModel",
    "TokenUsage",
    "ToolCall",
    "ToolCallDelta",
    "__version__",
]
