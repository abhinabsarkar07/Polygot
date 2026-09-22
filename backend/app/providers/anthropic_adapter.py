"""Anthropic Messages API adapter.

Named ``anthropic_adapter.py`` rather than ``anthropic.py`` to avoid
shadowing the ``anthropic`` package this module imports -- Python 3's
absolute-import default means ``import anthropic`` from inside a module
named ``anthropic.py`` would still resolve correctly, but the name
collision is confusing enough to avoid on sight.

Verified 2026-09-22 against the installed ``anthropic==1.7.0`` SDK's own
exception hierarchy (``anthropic._exceptions``) and
https://platform.claude.com/docs/en/build-with-claude/streaming +
https://platform.claude.com/docs/en/api/errors +
https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons.
See docs/PROVIDER_NOTES.md for the full reconciliation notes.
"""

import json
import logging
from collections.abc import AsyncIterator

import anthropic

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
    ToolResultBlock,
    ToolUseBlock,
)

logger = logging.getLogger(__name__)

# stop_reason -> FinishReason. `pause_turn` (server-tool sampling-loop pause)
# has no normalized equivalent and degrades to UNKNOWN rather than crashing
# on a value this mapping doesn't recognize -- see _normalize_finish_reason.
_FINISH_REASON = {
    "end_turn": FinishReason.STOP,
    "stop_sequence": FinishReason.STOP,
    "max_tokens": FinishReason.LENGTH,
    "model_context_window_exceeded": FinishReason.LENGTH,
    "tool_use": FinishReason.TOOL_USE,
    "refusal": FinishReason.CONTENT_FILTER,
}


class AnthropicAdapter(Provider):
    id = "anthropic"

    def __init__(self, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    # --- Provider interface -------------------------------------------------

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        try:
            message = await self._client.messages.create(**self._build_request(request))
        except Exception as exc:  # noqa: BLE001 -- translated immediately below
            raise self._translate_error(exc) from exc

        return CompletionResponse(
            model=request.model,
            content=self._normalize_content(message.content),
            usage=self._normalize_usage(message.usage),
            finish_reason=self._normalize_finish_reason(message.stop_reason),
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        # Per-content-block-index bookkeeping: which id/type each index is
        # (needed to attach the right tool_use id to later deltas) and the
        # raw JSON fragments accumulated so far for tool_use blocks, since
        # Anthropic streams tool input as partial JSON *strings*, not
        # incremental objects -- see the module docstring's streaming
        # reference. We only parse once content_block_stop tells us the
        # fragment is complete; parsing partial fragments early would often
        # be invalid JSON by design.
        block_kind: dict[int, str] = {}
        block_id: dict[int, str] = {}
        block_name: dict[int, str] = {}
        json_buffer: dict[int, str] = {}
        input_tokens: int | None = None
        stop_reason: str | None = None

        try:
            async with self._client.messages.stream(**self._build_request(request)) as stream:
                async for event in stream:
                    if event.type == "message_start":
                        input_tokens = event.message.usage.input_tokens

                    elif event.type == "content_block_start":
                        block_kind[event.index] = event.content_block.type
                        if event.content_block.type == "tool_use":
                            block_id[event.index] = event.content_block.id
                            block_name[event.index] = event.content_block.name
                            json_buffer[event.index] = ""
                            yield ToolUseStartEvent(id=event.content_block.id, name=event.content_block.name)

                    elif event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield TextDeltaEvent(text=event.delta.text)
                        elif event.delta.type == "input_json_delta":
                            json_buffer[event.index] += event.delta.partial_json
                            yield ToolUseDeltaEvent(id=block_id[event.index], partial_json=event.delta.partial_json)
                        # thinking_delta / signature_delta: extended thinking
                        # isn't part of the normalized contract yet (CP-02
                        # defined no ThinkingBlock/event) -- skipped, not
                        # crashed on.

                    elif event.type == "content_block_stop":
                        if block_kind.get(event.index) == "tool_use":
                            raw = json_buffer[event.index]
                            parsed = json.loads(raw) if raw else {}
                            yield ToolUseCompleteEvent(id=block_id[event.index], name=block_name[event.index], input=parsed)

                    elif event.type == "message_delta":
                        stop_reason = event.delta.stop_reason or stop_reason
                        yield UsageEvent(
                            usage=Usage(
                                input_tokens=input_tokens,
                                output_tokens=event.usage.output_tokens,
                                cached_input_tokens=getattr(event.usage, "cache_read_input_tokens", None),
                                reasoning_tokens=None,
                            )
                        )

                    elif event.type == "message_stop":
                        yield DoneEvent(finish_reason=self._normalize_finish_reason(stop_reason))

                    # `ping` and any future/unknown event types are ignored,
                    # per the docs' own versioning guidance to handle
                    # unrecognized event types gracefully rather than error.

        except Exception as exc:  # noqa: BLE001 -- normalized into an ErrorEvent, not raised
            error = self._translate_error(exc)
            logger.debug("anthropic stream error: %s", exc)
            yield ErrorEvent(kind=error.kind, message=error.message)

    # --- Request translation -------------------------------------------------

    def _build_request(self, request: CompletionRequest) -> dict:
        payload: dict = {
            "model": self._provider_model_id(request),
            "messages": [self._translate_message(m) for m in request.messages],
            "max_tokens": request.max_tokens,
        }
        if request.system is not None:
            payload["system"] = request.system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            payload["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in request.tools
            ]
        return payload

    def _provider_model_id(self, request: CompletionRequest) -> str:
        # The adapter is handed CompletionRequest.model, which by contract
        # (see app/providers/contracts.py) is our *internal* model id.
        # Resolving it to Anthropic's own model string is ModelRegistry's
        # job (app/providers/models.py) -- by the time a request reaches
        # this adapter, whatever constructed it is expected to have already
        # done that resolution and put the provider model id here. CP-04's
        # chat service is where that resolution actually happens; nothing
        # about that step belongs in the adapter.
        return request.model

    def _translate_message(self, message: Message) -> dict:
        # Anthropic has no third "tool" role on the wire -- a tool result is
        # sent back as a `tool_result` content block inside a *user* message,
        # immediately following the assistant message that contained the
        # matching `tool_use` block. Our Role.TOOL collapses into "user"
        # here; nothing upstream of this adapter needs to know that.
        role = "assistant" if message.role == Role.ASSISTANT else "user"
        return {"role": role, "content": [self._translate_block(b) for b in message.content]}

    def _translate_block(self, block: ContentBlock) -> dict:
        if isinstance(block, TextBlock):
            return {"type": "text", "text": block.text}
        if isinstance(block, ImageBlock):
            return {
                "type": "image",
                "source": {"type": "base64", "media_type": block.media_type, "data": block.data},
            }
        if isinstance(block, ToolUseBlock):
            return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
        if isinstance(block, ToolResultBlock):
            return {
                "type": "tool_result",
                "tool_use_id": block.tool_use_id,
                "content": block.content,
                "is_error": block.is_error,
            }
        raise AssertionError(f"unhandled content block type: {type(block)!r}")  # pragma: no cover

    # --- Response normalization ----------------------------------------------

    def _normalize_content(self, content: list) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        for block in content:
            if block.type == "text":
                blocks.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                blocks.append(ToolUseBlock(id=block.id, name=block.name, input=block.input))
            # `thinking`, `redacted_thinking`, `server_tool_use`,
            # `web_search_tool_result`: not part of the normalized contract
            # yet (no tool execution, no extended-thinking type exists in
            # CP-02's domain model) -- skipped rather than guessed at.
        return blocks

    def _normalize_usage(self, usage) -> Usage:
        return Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=getattr(usage, "cache_read_input_tokens", None),
            # Anthropic bills extended-thinking tokens as part of
            # output_tokens rather than reporting them as a separate
            # metric, so there is nothing to put here -- None ("not
            # reported"), not 0.
            reasoning_tokens=None,
        )

    def _normalize_finish_reason(self, stop_reason: str | None) -> FinishReason:
        if stop_reason is None:
            return FinishReason.UNKNOWN
        return _FINISH_REASON.get(stop_reason, FinishReason.UNKNOWN)

    # --- Error translation ----------------------------------------------------

    def _translate_error(self, exc: Exception) -> ProviderError:
        if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
            kind = ProviderErrorKind.AUTH
        elif isinstance(exc, anthropic.RateLimitError):
            kind = ProviderErrorKind.RATE_LIMIT
        elif isinstance(exc, anthropic.RequestTooLargeError):
            kind = ProviderErrorKind.CONTEXT_LENGTH
        elif isinstance(exc, anthropic.APITimeoutError | anthropic.DeadlineExceededError):
            kind = ProviderErrorKind.TIMEOUT
        elif isinstance(
            exc,
            anthropic.BadRequestError | anthropic.NotFoundError | anthropic.ConflictError | anthropic.UnprocessableEntityError,
        ):
            kind = ProviderErrorKind.BAD_REQUEST
        elif isinstance(exc, anthropic.APIStatusError | anthropic.APIConnectionError):
            # Covers OverloadedError (529), ServiceUnavailableError (503),
            # InternalServerError, and any other >=500 the SDK hasn't given
            # a more specific subclass to.
            kind = ProviderErrorKind.SERVER_ERROR
        else:
            kind = ProviderErrorKind.UNKNOWN
        return ProviderError(kind=kind, message=str(exc), provider=self.id)
