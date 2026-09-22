"""The request/response/streaming envelope around a conversation.

``messages.py`` defines what a message *is*; this module defines what it
means to ask a provider to complete one (:class:`CompletionRequest`), what
comes back (:class:`CompletionResponse`, or a stream of
:class:`StreamEvent`), and the normalized accounting/outcome types
(:class:`Usage`, :class:`FinishReason`) that both share.
"""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.providers.errors import ProviderErrorKind
from app.providers.messages import ContentBlock, Message, ToolDefinition


class Usage(BaseModel):
    """Token accounting for one completion.

    Every field is ``int | None``, not ``int = 0``, because a provider that
    doesn't report a metric at all (e.g. no ``reasoning_tokens`` in its
    response) is a different fact than a provider that reports the metric
    *as* zero. Collapsing both to ``0`` would make "this model has no
    reasoning tokens" indistinguishable from "we don't know" -- which
    matters the moment CP-06 tries to build a cost/usage dashboard from
    this data. ``None`` means "not reported"; ``0`` means "reported, and it
    was zero".
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    reasoning_tokens: int | None = None


class FinishReason(str, Enum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_USE = "tool_use"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    UNKNOWN = "unknown"


class CompletionRequest(BaseModel):
    """A provider-neutral request to complete a conversation.

    ``model`` is the exact model string a :class:`~app.providers.base.Provider`
    sends upstream as-is -- by construction time, it must already be
    ``ModelConfig.provider_model_id`` (see ``app/providers/models.py``),
    not our internal model id. (An earlier version of this docstring said
    the opposite; CP-04's live testing against a real provider caught the
    mismatch -- a request built with the internal id got a real 404 from
    Anthropic, since "claude-sonnet" isn't a model it knows about, only
    "claude-sonnet-5" is.) Internal ids belong on application-facing
    surfaces only -- the browser's model selector, ``ModelRegistry``'s own
    keys, a persisted message's ``model_id`` column -- never on this
    field. Resolving internal id -> ``provider_model_id`` is the one
    caller (``ChatService.prepare_turn``, CP-04) that holds both a
    ``ModelRegistry`` and constructs this request's job to do, once, in
    one place.

    No cancellation field: Python's native mechanism for "stop this
    in-flight async operation" is cancelling the ``asyncio.Task`` running
    it, which FastAPI already does when a request disconnects. A CP-04
    streaming endpoint cancels by cancelling the task iterating
    ``provider.stream()``; a well-behaved adapter's ``await
    http_client.post(...)`` unwinds on ``asyncio.CancelledError`` the same
    as any other awaited call, propagating the cancellation to the
    underlying HTTP request. Bolting a JavaScript-style ``AbortSignal``
    field onto this request would duplicate a mechanism Python already
    has, and would be one more thing every adapter has to remember to
    check.
    """

    model: str
    messages: list[Message]
    system: str | None = None
    tools: list[ToolDefinition] | None = None
    max_tokens: int
    temperature: float | None = None


class CompletionResponse(BaseModel):
    """A normalized, non-streaming completion result.

    No raw provider finish-reason string lives here on purpose -- keeping
    this model provider-agnostic means nothing in it should ever tempt
    application code into branching on provider-specific text. An adapter
    that wants the raw reason for debugging logs it directly (e.g.
    ``logger.debug("anthropic stop_reason=%s", raw)``) at the point of
    translation, rather than smuggling it through this contract.
    """

    model: str
    content: list[ContentBlock]
    usage: Usage
    finish_reason: FinishReason


# --- Streaming ---
#
# One flat, tagged model per event kind rather than a single StreamEvent
# with a dozen optional fields: a TextDeltaEvent can't accidentally carry a
# stray `usage` field, callers pattern-match on `event.type` (or isinstance)
# and get exactly the fields that event kind has, and adding a new event
# kind later is an additive Annotated union member, not a new optional
# field on a shared class.


class TextDeltaEvent(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolUseStartEvent(BaseModel):
    type: Literal["tool_use_start"] = "tool_use_start"
    id: str
    name: str


class ToolUseDeltaEvent(BaseModel):
    """A fragment of the tool call's JSON input as the provider streams it
    in. ``partial_json`` is the raw incremental text, not yet parsed --
    providers stream tool input as it's generated, often mid-object, so
    accumulating and parsing the full string is the caller's job once
    ToolUseCompleteEvent arrives with the parsed whole."""

    type: Literal["tool_use_delta"] = "tool_use_delta"
    id: str
    partial_json: str


class ToolUseCompleteEvent(BaseModel):
    type: Literal["tool_use_complete"] = "tool_use_complete"
    id: str
    name: str
    input: dict


class UsageEvent(BaseModel):
    type: Literal["usage"] = "usage"
    usage: Usage


class DoneEvent(BaseModel):
    type: Literal["done"] = "done"
    finish_reason: FinishReason


class ErrorEvent(BaseModel):
    """Carries the same normalized ``kind``/``message`` a raised
    :class:`~app.providers.errors.ProviderError` would, as plain data
    rather than the exception instance -- this event is what eventually
    gets serialized onto an SSE stream (CP-04), and an ``Exception`` isn't
    something you JSON-encode."""

    type: Literal["error"] = "error"
    kind: ProviderErrorKind
    message: str


StreamEvent = Annotated[
    TextDeltaEvent
    | ToolUseStartEvent
    | ToolUseDeltaEvent
    | ToolUseCompleteEvent
    | UsageEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="type"),
]
