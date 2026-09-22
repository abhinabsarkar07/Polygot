"""The provider-neutral shape of a conversation.

Everything here describes *what was said*, never *how a specific provider's
API represents it*. An Anthropic content block, a Gemini ``Part``, and an
OpenAI Responses API content object all eventually get translated into one
of these types by an adapter (CP-03+) -- application code (chat services,
API routes, CP-05's RAG service) only ever sees these.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field
from enum import Enum


class Role(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(BaseModel):
    """Deliberately minimal: base64 bytes plus the media type needed to
    reconstruct a data URI or provider-specific image payload. Adapters
    decide how their provider's API wants images framed -- this block only
    has to carry enough information for that translation to be possible."""

    type: Literal["image"] = "image"
    media_type: str  # e.g. "image/png", "image/jpeg"
    data: str  # base64-encoded image bytes


class ToolUseBlock(BaseModel):
    """The model asking to invoke a tool. ``id`` correlates this to the
    ToolResultBlock that answers it, the same way every provider's own
    tool-calling protocol correlates a call to its result."""

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolResultBlock(BaseModel):
    """The application's answer to a prior ToolUseBlock. ``content`` is
    plain text for CP-02 -- a tool result that itself contains an image is
    a real possibility for some providers, but no tool is actually
    implemented until a later checkpoint, so a list-of-blocks generalization
    is deferred until something concrete needs it."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


ContentBlock = Annotated[
    TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock,
    Field(discriminator="type"),
]


class Message(BaseModel):
    role: Role
    content: list[ContentBlock]


class ToolDefinition(BaseModel):
    """What a tool is, not what it does. Defining this now (with no tool
    actually implemented) lets CompletionRequest and the streaming
    tool_use_* events exist in CP-02 without any adapter needing to guess
    at the shape later."""

    name: str
    description: str
    input_schema: dict[str, Any]
