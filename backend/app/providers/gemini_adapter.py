"""Gemini adapter, built on the current official ``google-genai`` SDK
(``pip install google-genai``) -- **not** the deprecated
``google-generativeai`` package, which Google's own migration guide
(ai.google.dev/gemini-api/docs/migrate) says is legacy as of this check
(2026-09-22) and receives no new features.

Gemini's shape differs from both Anthropic's and OpenAI's in ways that stay
entirely inside this adapter:

- Only two roles exist on the wire, ``user`` and ``model`` -- no
  ``assistant``, no third role for tool results (see ``_translate_message``).
- Tool declarations accept a raw JSON Schema directly via
  ``FunctionDeclaration.parameters_json_schema`` (confirmed in the
  installed SDK's ``types.py``), so translation there is closer to a
  passthrough than Anthropic's or OpenAI's.
- Function-call arguments arrive as an already-parsed dict, not a stream of
  partial JSON fragments -- the installed SDK's own type definitions mark
  fragment-level streaming (``FunctionCall.partial_args``,
  ``PartialArg``) explicitly "not supported in Gemini API". See
  ``stream()`` and docs/PROVIDER_NOTES.md.
- The SDK's error hierarchy is flat: ``ClientError`` (4xx) and
  ``ServerError`` (5xx), both carrying a numeric ``.code`` -- there is no
  per-status-code exception class the way Anthropic/OpenAI provide, so this
  adapter branches on the code itself (see ``_translate_error``).

Verified against the installed ``google-genai==2.24.0`` SDK's own type
definitions (``google/genai/types.py``) and exception classes
(``google/genai/errors.py``).
"""

import logging
from collections.abc import AsyncIterator

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

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

# FinishReason (google.genai.types) -> our FinishReason. Gemini has no
# distinct "the model wants to call a tool" reason -- a function-call turn
# still reports STOP (or nothing) at the API level, so TOOL_USE is detected
# by inspecting the response content instead; see _normalize_finish_reason.
_FINISH_REASON = {
    "STOP": FinishReason.STOP,
    "MAX_TOKENS": FinishReason.LENGTH,
    "SAFETY": FinishReason.CONTENT_FILTER,
    "RECITATION": FinishReason.CONTENT_FILTER,
    "BLOCKLIST": FinishReason.CONTENT_FILTER,
    "PROHIBITED_CONTENT": FinishReason.CONTENT_FILTER,
    "SPII": FinishReason.CONTENT_FILTER,
    "IMAGE_SAFETY": FinishReason.CONTENT_FILTER,
}

# APIError.code (an HTTP status int) -> our ProviderErrorKind. Unlike
# Anthropic/OpenAI's typed exception subclasses, this SDK gives every HTTP
# error the same two classes (ClientError/ServerError) and expects callers
# to branch on the numeric code themselves -- this dict *is* that branch.
_ERROR_KIND_BY_CODE = {
    401: ProviderErrorKind.AUTH,
    403: ProviderErrorKind.AUTH,
    400: ProviderErrorKind.BAD_REQUEST,
    404: ProviderErrorKind.BAD_REQUEST,
    409: ProviderErrorKind.BAD_REQUEST,
    413: ProviderErrorKind.CONTEXT_LENGTH,
    429: ProviderErrorKind.RATE_LIMIT,
    408: ProviderErrorKind.TIMEOUT,
    504: ProviderErrorKind.TIMEOUT,
}


class GeminiAdapter(Provider):
    id = "gemini"

    def __init__(self, api_key: str) -> None:
        self._client = genai.Client(api_key=api_key)

    # --- Provider interface -------------------------------------------------

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        try:
            response = await self._client.aio.models.generate_content(
                model=request.model,
                contents=self._translate_contents(request.messages),
                config=self._build_config(request),
            )
        except Exception as exc:  # noqa: BLE001 -- translated immediately below
            raise self._translate_error(exc) from exc

        candidate = response.candidates[0]
        return CompletionResponse(
            model=request.model,
            content=self._normalize_parts(candidate.content.parts if candidate.content else []),
            usage=self._normalize_usage(response.usage_metadata),
            finish_reason=self._normalize_finish_reason(candidate),
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        saw_function_call = False
        raw_finish_reason: str | None = None
        usage = None

        try:
            stream = await self._client.aio.models.generate_content_stream(
                model=request.model,
                contents=self._translate_contents(request.messages),
                config=self._build_config(request),
            )
            async for chunk in stream:
                if chunk.usage_metadata is not None:
                    usage = chunk.usage_metadata

                candidate = chunk.candidates[0] if chunk.candidates else None
                if candidate is None:
                    continue
                if candidate.finish_reason is not None:
                    raw_finish_reason = candidate.finish_reason.value

                for part in candidate.content.parts if candidate.content else []:
                    if part.text:
                        yield TextDeltaEvent(text=part.text)
                    elif part.function_call:
                        saw_function_call = True
                        call_id = part.function_call.id or part.function_call.name
                        # No fragment-level streaming for tool arguments on
                        # this provider (see module docstring) -- the whole
                        # parsed `args` dict arrives in one chunk, so Start
                        # and Complete fire back-to-back with no Delta
                        # between them, rather than pretending Gemini
                        # streamed something it didn't.
                        yield ToolUseStartEvent(id=call_id, name=part.function_call.name)
                        yield ToolUseCompleteEvent(id=call_id, name=part.function_call.name, input=part.function_call.args or {})

            if usage is not None:
                yield UsageEvent(usage=self._normalize_usage(usage))
            yield DoneEvent(finish_reason=self._normalize_finish_reason_from_raw(raw_finish_reason, saw_function_call))

        except Exception as exc:  # noqa: BLE001 -- normalized into an ErrorEvent, not raised
            error = self._translate_error(exc)
            logger.debug("gemini stream error: %s", exc)
            yield ErrorEvent(kind=error.kind, message=error.message)

    # --- Request translation -------------------------------------------------

    def _build_config(self, request: CompletionRequest) -> genai_types.GenerateContentConfig:
        config = genai_types.GenerateContentConfig(max_output_tokens=request.max_tokens)
        if request.system is not None:
            config.system_instruction = request.system
        if request.temperature is not None:
            config.temperature = request.temperature
        if request.tools:
            config.tools = [
                genai_types.Tool(
                    function_declarations=[
                        genai_types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.input_schema)
                        for t in request.tools
                    ]
                )
            ]
        return config

    def _translate_contents(self, messages: list[Message]) -> list[genai_types.Content]:
        # Gemini's FunctionResponse requires the *name* of the function it
        # answers, but our ToolResultBlock only carries the correlating id
        # (tool_use_id) -- matching Anthropic's and OpenAI's tool-result
        # shapes, which need no name. Rather than widen the CP-02 contract
        # for one provider's requirement, this adapter recovers the name by
        # looking back through the same request's own message history for
        # the ToolUseBlock that originated the call -- it's already there,
        # a few messages earlier, in every real conversation.
        tool_name_by_id = {
            block.id: block.name for m in messages for block in m.content if isinstance(block, ToolUseBlock)
        }
        return [self._translate_message(m, tool_name_by_id) for m in messages]

    def _translate_message(self, message: Message, tool_name_by_id: dict[str, str]) -> genai_types.Content:
        role = "model" if message.role == Role.ASSISTANT else "user"
        parts = [self._translate_block(b, tool_name_by_id) for b in message.content]
        return genai_types.Content(role=role, parts=parts)

    def _translate_block(self, block: ContentBlock, tool_name_by_id: dict[str, str]) -> genai_types.Part:
        if isinstance(block, TextBlock):
            return genai_types.Part.from_text(text=block.text)
        if isinstance(block, ImageBlock):
            import base64

            return genai_types.Part.from_bytes(data=base64.b64decode(block.data), mime_type=block.media_type)
        if isinstance(block, ToolUseBlock):
            return genai_types.Part.from_function_call(name=block.name, args=block.input)
        if isinstance(block, ToolResultBlock):
            name = tool_name_by_id.get(block.tool_use_id, block.tool_use_id)
            response = {"error": block.content} if block.is_error else {"output": block.content}
            return genai_types.Part.from_function_response(name=name, response=response)
        raise AssertionError(f"unhandled content block type: {type(block)!r}")  # pragma: no cover

    # --- Response normalization ----------------------------------------------

    def _normalize_parts(self, parts: list) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        for part in parts:
            if part.text:
                blocks.append(TextBlock(text=part.text))
            elif part.function_call:
                call_id = part.function_call.id or part.function_call.name
                blocks.append(ToolUseBlock(id=call_id, name=part.function_call.name, input=part.function_call.args or {}))
            # inline_data (model-generated images), executable_code,
            # code_execution_result: out of scope for CP-03's normalized
            # contract, skipped.
        return blocks

    def _normalize_usage(self, usage_metadata) -> Usage:
        if usage_metadata is None:
            return Usage()
        return Usage(
            input_tokens=usage_metadata.prompt_token_count,
            output_tokens=usage_metadata.candidates_token_count,
            cached_input_tokens=usage_metadata.cached_content_token_count,
            # Gemini's usage_metadata reports no separate reasoning-token
            # count -- None ("not reported"), not 0.
            reasoning_tokens=None,
        )

    def _normalize_finish_reason(self, candidate) -> FinishReason:
        has_function_call = any(
            getattr(part, "function_call", None) is not None for part in (candidate.content.parts if candidate.content else [])
        )
        raw = candidate.finish_reason.value if candidate.finish_reason else None
        return self._normalize_finish_reason_from_raw(raw, has_function_call)

    def _normalize_finish_reason_from_raw(self, raw: str | None, saw_function_call: bool) -> FinishReason:
        if saw_function_call:
            return FinishReason.TOOL_USE
        if raw is None:
            return FinishReason.UNKNOWN
        return _FINISH_REASON.get(raw, FinishReason.UNKNOWN)

    # --- Error translation ----------------------------------------------------

    def _translate_error(self, exc: Exception) -> ProviderError:
        if isinstance(exc, genai_errors.APIError):
            kind = _ERROR_KIND_BY_CODE.get(exc.code)
            if kind is None:
                kind = ProviderErrorKind.SERVER_ERROR if isinstance(exc, genai_errors.ServerError) else ProviderErrorKind.BAD_REQUEST
        else:
            kind = ProviderErrorKind.UNKNOWN
        return ProviderError(kind=kind, message=str(exc), provider=self.id)
