"""Model provider boundary for Code Agent."""

from .fake import FakeProvider
from .provider import (
    CancellationToken,
    ModelCapability,
    ModelProvider,
    ModelProviderError,
    NeverCancelToken,
    ProviderErrorCode,
    ProviderInfo,
    RetryingProvider,
    RetryPolicy,
)
from .providers import OpenAICompatibleConfig, OpenAICompatibleProvider
from .registry import ProviderRegistry, ProviderRegistryError
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
    "CancellationToken",
    "FakeProvider",
    "FinishReason",
    "GenerationConfig",
    "Message",
    "MessageRole",
    "ModelCapability",
    "ModelEvent",
    "ModelEventType",
    "ModelProvider",
    "ModelProviderError",
    "ModelRequest",
    "ModelResponse",
    "ModelToolSpec",
    "NeverCancelToken",
    "OpenAICompatibleConfig",
    "OpenAICompatibleProvider",
    "ProtocolModel",
    "ProviderErrorCode",
    "ProviderInfo",
    "ProviderRegistry",
    "ProviderRegistryError",
    "RetryPolicy",
    "RetryingProvider",
    "TokenUsage",
    "ToolCall",
    "ToolCallDelta",
    "__version__",
]
