"""Provider-neutral AI contracts: the internal representation every part of
the application (chat service, RAG service, API routes) depends on instead
of any provider's own SDK types. See docs/DESIGN.md, "Provider Abstraction".
"""

from app.providers.base import Provider
from app.providers.contracts import (
    CompletionRequest,
    CompletionResponse,
    DoneEvent,
    ErrorEvent,
    FinishReason,
    StreamEvent,
    TextDeltaEvent,
    ToolUseCompleteEvent,
    ToolUseDeltaEvent,
    ToolUseStartEvent,
    Usage,
    UsageEvent,
)
from app.providers.errors import ProviderError, ProviderErrorKind
from app.providers.messages import (
    ContentBlock,
    ImageBlock,
    Message,
    Role,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)
from app.providers.models import (
    ModelCapabilities,
    ModelConfig,
    ModelNotFoundError,
    ModelRegistry,
    PricingConfig,
)
from app.providers.registry import ProviderNotFoundError, ProviderRegistry

__all__ = [
    "CompletionRequest",
    "CompletionResponse",
    "ContentBlock",
    "DoneEvent",
    "ErrorEvent",
    "FinishReason",
    "ImageBlock",
    "Message",
    "ModelCapabilities",
    "ModelConfig",
    "ModelNotFoundError",
    "ModelRegistry",
    "PricingConfig",
    "Provider",
    "ProviderError",
    "ProviderErrorKind",
    "ProviderNotFoundError",
    "ProviderRegistry",
    "Role",
    "StreamEvent",
    "TextBlock",
    "TextDeltaEvent",
    "ToolDefinition",
    "ToolResultBlock",
    "ToolUseBlock",
    "ToolUseCompleteEvent",
    "ToolUseDeltaEvent",
    "ToolUseStartEvent",
    "Usage",
    "UsageEvent",
]
