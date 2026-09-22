"""OpenAI adapter, built on the **Responses API** (`client.responses`),
not Chat Completions.

Verified 2026-09-22: OpenAI's own migration guide
(developers.openai.com/api/docs/guides/migrate-to-responses) states
Responses is recommended for all new projects, and Chat Completions is the
legacy shape kept for backward compatibility. The Responses API represents
a conversation as an ``input``/``output`` array of typed *items*
(``message``, ``function_call``, ``function_call_output``, ...) rather than
Chat Completions' flat list of role/content messages -- a real structural
difference from both Anthropic and Gemini that this adapter absorbs;
nothing upstream of it needs to know.

Verified against the installed ``openai==3.17.0`` SDK's own type
definitions (``openai/types/responses/*.py``) and exception hierarchy
(``openai._exceptions``). See docs/PROVIDER_NOTES.md for full reconciliation
notes, including why ``ContentFilterFinishReasonError`` /
``LengthFinishReasonError`` (real SDK exception classes) do not appear in
this adapter's error translation -- they're raised only by the SDK's
``.parse()`` structured-output helper, which this adapter doesn't use.
"""

import json
import logging
from collections.abc import AsyncIterator

import openai

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

# Response.incomplete_details.reason -> FinishReason. `steered` (WebSocket
# steering) and `max_messages` have no close normalized equivalent for a
# plain request/response or SSE adapter and degrade to UNKNOWN/LENGTH
# respectively rather than crashing on a value not in this mapping.
_INCOMPLETE_REASON = {
    "max_output_tokens": FinishReason.LENGTH,
    "max_messages": FinishReason.LENGTH,
    "content_filter": FinishReason.CONTENT_FILTER,
}


class OpenAIAdapter(Provider):
    id = "openai"

    def __init__(self, api_key: str) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)

    # --- Provider interface -------------------------------------------------

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        try:
            response = await self._client.responses.create(**self._build_request(request))
        except Exception as exc:  # noqa: BLE001 -- translated immediately below
            raise self._translate_error(exc) from exc

        return CompletionResponse(
            model=request.model,
            content=self._normalize_output(response.output),
            usage=self._normalize_usage(response.usage),
            finish_reason=self._normalize_finish_reason(response),
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        # Responses API streaming is item-based: a function_call's call_id
        # (the id a subsequent function_call_output must reference) is only
        # given once, on response.output_item.added -- the delta/done events
        # that follow key off item_id instead. This tracks item_id ->
        # (call_id, name) so later deltas can carry the right normalized id.
        call_id_by_item: dict[str, str] = {}
        name_by_item: dict[str, str] = {}

        try:
            stream = await self._client.responses.create(stream=True, **self._build_request(request))
            async for event in stream:
                if event.type == "response.output_item.added" and event.item.type == "function_call":
                    # `item.id` is what later delta/done events key off
                    # (their `item_id` field); `item.call_id` is the
                    # correlator a function_call_output must reference. The
                    # two are different ids for the same tool call -- this
                    # map translates between them.
                    item_key = event.item.id or event.item.call_id
                    call_id_by_item[item_key] = event.item.call_id
                    name_by_item[item_key] = event.item.name
                    yield ToolUseStartEvent(id=event.item.call_id, name=event.item.name)

                elif event.type == "response.output_text.delta":
                    yield TextDeltaEvent(text=event.delta)

                elif event.type == "response.function_call_arguments.delta":
                    call_id = call_id_by_item.get(event.item_id, event.item_id)
                    yield ToolUseDeltaEvent(id=call_id, partial_json=event.delta)

                elif event.type == "response.function_call_arguments.done":
                    call_id = call_id_by_item.get(event.item_id, event.item_id)
                    name = name_by_item.get(event.item_id, "")
                    parsed = json.loads(event.arguments) if event.arguments else {}
                    yield ToolUseCompleteEvent(id=call_id, name=name, input=parsed)

                elif event.type in ("response.completed", "response.failed", "response.incomplete"):
                    yield UsageEvent(usage=self._normalize_usage(event.response.usage))
                    yield DoneEvent(finish_reason=self._normalize_finish_reason(event.response))

                elif event.type == "error":
                    yield ErrorEvent(kind=self._error_kind_from_code(event.code), message=event.message)

                # Every other event type (audio, reasoning, mcp,
                # code_interpreter, image_gen, shell, web_search, ...) is
                # outside the normalized contract for CP-03 and is skipped,
                # not errored on.

        except Exception as exc:  # noqa: BLE001 -- normalized into an ErrorEvent, not raised
            error = self._translate_error(exc)
            logger.debug("openai stream error: %s", exc)
            yield ErrorEvent(kind=error.kind, message=error.message)

    # --- Request translation -------------------------------------------------

    def _build_request(self, request: CompletionRequest) -> dict:
        payload: dict = {
            "model": request.model,
            "input": self._translate_input(request.messages),
            "max_output_tokens": request.max_tokens,
        }
        if request.system is not None:
            payload["instructions"] = request.system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                    "strict": None,
                }
                for t in request.tools
            ]
        return payload

    def _translate_input(self, messages: list[Message]) -> list[dict]:
        items: list[dict] = []
        for message in messages:
            if message.role == Role.TOOL:
                for block in message.content:
                    if isinstance(block, ToolResultBlock):
                        # No native is_error flag on function_call_output
                        # (unlike Anthropic's tool_result.is_error) -- see
                        # docs/PROVIDER_NOTES.md.
                        items.append({"type": "function_call_output", "call_id": block.tool_use_id, "output": block.content})
                continue

            role = "assistant" if message.role == Role.ASSISTANT else "user"
            content_parts = [self._translate_content_part(b) for b in message.content if isinstance(b, TextBlock | ImageBlock)]
            if content_parts:
                items.append({"type": "message", "role": role, "content": content_parts})
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    items.append(
                        {"type": "function_call", "call_id": block.id, "name": block.name, "arguments": json.dumps(block.input)}
                    )
        return items

    def _translate_content_part(self, block: ContentBlock) -> dict:
        if isinstance(block, TextBlock):
            return {"type": "input_text", "text": block.text}
        if isinstance(block, ImageBlock):
            return {"type": "input_image", "image_url": f"data:{block.media_type};base64,{block.data}"}
        raise AssertionError(f"unhandled content block type: {type(block)!r}")  # pragma: no cover

    # --- Response normalization ----------------------------------------------

    def _normalize_output(self, output: list) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        for item in output:
            if item.type == "message":
                for part in item.content:
                    if part.type == "output_text":
                        blocks.append(TextBlock(text=part.text))
                    # `refusal` content parts: not part of the normalized
                    # contract -- the refusal is reflected in finish_reason
                    # via incomplete_details instead, not duplicated here.
            elif item.type == "function_call":
                parsed = json.loads(item.arguments) if item.arguments else {}
                blocks.append(ToolUseBlock(id=item.call_id, name=item.name, input=parsed))
            # reasoning / mcp_call / code_interpreter_call / ... items: out
            # of scope for CP-03's normalized contract, skipped.
        return blocks

    def _normalize_usage(self, usage) -> Usage:
        if usage is None:
            return Usage()
        return Usage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=usage.input_tokens_details.cached_tokens if usage.input_tokens_details else None,
            reasoning_tokens=usage.output_tokens_details.reasoning_tokens if usage.output_tokens_details else None,
        )

    def _normalize_finish_reason(self, response) -> FinishReason:
        if response.status == "failed":
            return FinishReason.ERROR
        if response.status == "incomplete" and response.incomplete_details:
            return _INCOMPLETE_REASON.get(response.incomplete_details.reason, FinishReason.UNKNOWN)
        if any(item.type == "function_call" for item in response.output):
            return FinishReason.TOOL_USE
        if response.status == "completed":
            return FinishReason.STOP
        return FinishReason.UNKNOWN

    def _error_kind_from_code(self, code: str | None) -> ProviderErrorKind:
        if not code:
            return ProviderErrorKind.UNKNOWN
        code = code.lower()
        if "rate_limit" in code:
            return ProviderErrorKind.RATE_LIMIT
        if "content_filter" in code or "moderation" in code:
            return ProviderErrorKind.CONTENT_FILTER
        if "context" in code or "token" in code:
            return ProviderErrorKind.CONTEXT_LENGTH
        return ProviderErrorKind.SERVER_ERROR

    # --- Error translation ----------------------------------------------------

    def _translate_error(self, exc: Exception) -> ProviderError:
        if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError | openai.OAuthError):
            kind = ProviderErrorKind.AUTH
        elif isinstance(exc, openai.RateLimitError):
            kind = ProviderErrorKind.RATE_LIMIT
        elif isinstance(exc, openai.APITimeoutError):
            kind = ProviderErrorKind.TIMEOUT
        elif isinstance(
            exc, openai.BadRequestError | openai.NotFoundError | openai.ConflictError | openai.UnprocessableEntityError
        ):
            kind = ProviderErrorKind.BAD_REQUEST
        elif isinstance(exc, openai.APIStatusError | openai.APIConnectionError | openai.InternalServerError):
            kind = ProviderErrorKind.SERVER_ERROR
        else:
            kind = ProviderErrorKind.UNKNOWN
        return ProviderError(kind=kind, message=str(exc), provider=self.id)
