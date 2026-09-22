"""Gemini adapter tests. No network access, no API key -- shapes verified
directly from the installed google-genai==2.24.0 package (see
gemini_adapter.py's module docstring for sources). Fixture testing, not
live testing -- see docs/PROVIDER_NOTES.md.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.genai import errors as genai_errors

from app.providers.contracts import CompletionRequest, DoneEvent, ErrorEvent, FinishReason, TextDeltaEvent, ToolUseCompleteEvent, ToolUseStartEvent, UsageEvent
from app.providers.errors import ProviderErrorKind
from app.providers.gemini_adapter import GeminiAdapter
from app.providers.messages import Message, Role, TextBlock, ToolDefinition, ToolResultBlock, ToolUseBlock


@pytest.fixture
def adapter() -> GeminiAdapter:
    return GeminiAdapter(api_key="test-key")


def _part(**kwargs):
    kwargs.setdefault("text", None)
    kwargs.setdefault("function_call", None)
    return SimpleNamespace(**kwargs)


def _api_error(code: int, message: str = "x") -> genai_errors.APIError:
    return genai_errors.APIError(code, {"error": {"code": code, "message": message, "status": "x"}})


# --- Request translation ----------------------------------------------------


def test_translates_system_prompt_to_system_instruction(adapter):
    request = CompletionRequest(
        model="gemini-2.5-flash", system="Be concise.", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100
    )
    config = adapter._build_config(request)
    assert config.system_instruction == "Be concise."


def test_translates_assistant_role_to_model_not_assistant():
    request = CompletionRequest(
        model="gemini-2.5-flash",
        messages=[
            Message(role=Role.USER, content=[TextBlock(text="hi")]),
            Message(role=Role.ASSISTANT, content=[TextBlock(text="hello")]),
        ],
        max_tokens=100,
    )
    contents = GeminiAdapter(api_key="k")._translate_contents(request.messages)
    assert [c.role for c in contents] == ["user", "model"]


def test_translates_tool_role_to_user_with_function_response_part():
    # ToolResultBlock only carries the correlating id -- the adapter must
    # recover the function *name* Gemini requires from the earlier
    # ToolUseBlock in the same request's own history.
    request = CompletionRequest(
        model="gemini-2.5-flash",
        messages=[
            Message(role=Role.ASSISTANT, content=[ToolUseBlock(id="call_1", name="get_weather", input={"location": "Boston"})]),
            Message(role=Role.TOOL, content=[ToolResultBlock(tool_use_id="call_1", content="15C and cloudy")]),
        ],
        max_tokens=100,
    )
    contents = GeminiAdapter(api_key="k")._translate_contents(request.messages)
    tool_content = contents[1]
    assert tool_content.role == "user"
    part = tool_content.parts[0]
    assert part.function_response.name == "get_weather"
    assert part.function_response.response == {"output": "15C and cloudy"}


def test_translates_max_tokens_and_temperature():
    request = CompletionRequest(
        model="gemini-2.5-flash", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=512, temperature=0.3
    )
    config = GeminiAdapter(api_key="k")._build_config(request)
    assert config.max_output_tokens == 512
    assert config.temperature == 0.3


def test_translates_tool_definitions_via_raw_json_schema_passthrough():
    request = CompletionRequest(
        model="gemini-2.5-flash",
        messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])],
        tools=[ToolDefinition(name="get_weather", description="Get weather", input_schema={"type": "object"})],
        max_tokens=100,
    )
    config = GeminiAdapter(api_key="k")._build_config(request)
    declaration = config.tools[0].function_declarations[0]
    assert declaration.name == "get_weather"
    assert declaration.parameters_json_schema == {"type": "object"}


# --- Response normalization (complete) --------------------------------------


async def test_complete_normalizes_text_usage_and_finish_reason(adapter):
    candidate = SimpleNamespace(
        content=SimpleNamespace(parts=[_part(text="Hello there")]),
        finish_reason=SimpleNamespace(value="STOP"),
    )
    fake_response = SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(prompt_token_count=10, candidates_token_count=5, cached_content_token_count=None),
    )
    adapter._client.aio.models.generate_content = AsyncMock(return_value=fake_response)

    response = await adapter.complete(
        CompletionRequest(model="gemini-2.5-flash", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
    )

    assert response.content == [TextBlock(text="Hello there")]
    assert response.usage.input_tokens == 10
    assert response.usage.reasoning_tokens is None
    assert response.finish_reason == FinishReason.STOP


async def test_complete_detects_tool_use_from_content_not_finish_reason(adapter):
    # Gemini's FinishReason enum has no distinct "called a tool" value --
    # STOP is what's reported even when the model is calling a function.
    candidate = SimpleNamespace(
        content=SimpleNamespace(parts=[_part(function_call=SimpleNamespace(id="call_1", name="get_weather", args={"location": "Boston"}))]),
        finish_reason=SimpleNamespace(value="STOP"),
    )
    fake_response = SimpleNamespace(
        candidates=[candidate],
        usage_metadata=SimpleNamespace(prompt_token_count=20, candidates_token_count=8, cached_content_token_count=4),
    )
    adapter._client.aio.models.generate_content = AsyncMock(return_value=fake_response)

    response = await adapter.complete(
        CompletionRequest(model="gemini-2.5-flash", messages=[Message(role=Role.USER, content=[TextBlock(text="weather?")])], max_tokens=100)
    )

    assert response.content == [ToolUseBlock(id="call_1", name="get_weather", input={"location": "Boston"})]
    assert response.usage.cached_input_tokens == 4
    assert response.finish_reason == FinishReason.TOOL_USE


async def test_complete_maps_safety_finish_reason_to_content_filter(adapter):
    candidate = SimpleNamespace(content=SimpleNamespace(parts=[]), finish_reason=SimpleNamespace(value="SAFETY"))
    fake_response = SimpleNamespace(
        candidates=[candidate], usage_metadata=SimpleNamespace(prompt_token_count=5, candidates_token_count=0, cached_content_token_count=None)
    )
    adapter._client.aio.models.generate_content = AsyncMock(return_value=fake_response)

    response = await adapter.complete(
        CompletionRequest(model="gemini-2.5-flash", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
    )

    assert response.finish_reason == FinishReason.CONTENT_FILTER


# --- Streaming ----------------------------------------------------------------


class _AsyncChunkIterable:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for chunk in self._chunks:
            yield chunk


def _chunk(parts, finish_reason=None, usage_metadata=None):
    candidate = SimpleNamespace(content=SimpleNamespace(parts=parts), finish_reason=SimpleNamespace(value=finish_reason) if finish_reason else None)
    return SimpleNamespace(candidates=[candidate], usage_metadata=usage_metadata)


async def test_stream_normalizes_text_deltas(adapter):
    chunks = [
        _chunk([_part(text="Hel")]),
        _chunk([_part(text="lo")]),
        _chunk(
            [],
            finish_reason="STOP",
            usage_metadata=SimpleNamespace(prompt_token_count=5, candidates_token_count=2, cached_content_token_count=None),
        ),
    ]
    adapter._client.aio.models.generate_content_stream = AsyncMock(return_value=_AsyncChunkIterable(chunks))

    request = CompletionRequest(model="gemini-2.5-flash", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
    collected = [e async for e in adapter.stream(request)]

    text_events = [e for e in collected if isinstance(e, TextDeltaEvent)]
    assert [e.text for e in text_events] == ["Hel", "lo"]
    assert isinstance(collected[-1], DoneEvent)
    assert collected[-1].finish_reason == FinishReason.STOP


async def test_stream_emits_start_then_complete_with_no_delta_for_tool_calls(adapter):
    # Confirmed from the installed SDK's own types (PartialArg / FunctionCall
    # .partial_args are explicitly "not supported in Gemini API"): the whole
    # parsed args dict arrives in one chunk, so there is no fragment to emit
    # a ToolUseDeltaEvent for, unlike Anthropic/OpenAI.
    chunks = [
        _chunk([_part(function_call=SimpleNamespace(id="call_1", name="get_weather", args={"location": "Boston"}))]),
        _chunk(
            [],
            finish_reason="STOP",
            usage_metadata=SimpleNamespace(prompt_token_count=5, candidates_token_count=9, cached_content_token_count=None),
        ),
    ]
    adapter._client.aio.models.generate_content_stream = AsyncMock(return_value=_AsyncChunkIterable(chunks))

    request = CompletionRequest(model="gemini-2.5-flash", messages=[Message(role=Role.USER, content=[TextBlock(text="weather?")])], max_tokens=100)
    collected = [e for e in [e async for e in adapter.stream(request)] if not isinstance(e, UsageEvent)]

    assert isinstance(collected[0], ToolUseStartEvent)
    assert collected[0].id == "call_1"
    assert isinstance(collected[1], ToolUseCompleteEvent)
    assert collected[1].input == {"location": "Boston"}
    assert isinstance(collected[2], DoneEvent)
    assert collected[2].finish_reason == FinishReason.TOOL_USE  # detected from content, not Gemini's own STOP


async def test_stream_yields_error_event_instead_of_raising(adapter):
    async def _raise(**kwargs):
        raise genai_errors.ClientError(429, {"error": {"code": 429, "message": "slow down", "status": "RESOURCE_EXHAUSTED"}})

    adapter._client.aio.models.generate_content_stream = _raise

    request = CompletionRequest(model="gemini-2.5-flash", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
    collected = [e async for e in adapter.stream(request)]

    assert len(collected) == 1
    assert isinstance(collected[0], ErrorEvent)
    assert collected[0].kind == ProviderErrorKind.RATE_LIMIT


# --- Error normalization ------------------------------------------------------
#
# Gemini's SDK gives every HTTP error the same two classes (ClientError for
# 4xx, ServerError for 5xx) rather than Anthropic/OpenAI's typed
# per-status subclasses -- so this adapter's translation branches on the
# numeric `.code` itself, and these tests exercise that branch directly
# rather than relying on distinct exception *types* the SDK doesn't have.


@pytest.mark.parametrize(
    ("code", "expected_kind"),
    [
        (401, ProviderErrorKind.AUTH),
        (403, ProviderErrorKind.AUTH),
        (429, ProviderErrorKind.RATE_LIMIT),
        (400, ProviderErrorKind.BAD_REQUEST),
        (413, ProviderErrorKind.CONTEXT_LENGTH),
        (504, ProviderErrorKind.TIMEOUT),
        (500, ProviderErrorKind.SERVER_ERROR),
        (503, ProviderErrorKind.SERVER_ERROR),
    ],
)
async def test_complete_translates_status_codes_to_normalized_kinds(adapter, code, expected_kind):
    error_cls = genai_errors.ClientError if code < 500 else genai_errors.ServerError

    async def _raise(**kwargs):
        raise error_cls(code, {"error": {"code": code, "message": "x", "status": "x"}})

    adapter._client.aio.models.generate_content = _raise

    with pytest.raises(Exception) as exc_info:
        await adapter.complete(
            CompletionRequest(model="gemini-2.5-flash", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
        )

    assert exc_info.value.kind == expected_kind
    assert exc_info.value.provider == "gemini"


async def test_complete_translates_unrelated_exception_to_unknown(adapter):
    async def _raise(**kwargs):
        raise RuntimeError("something unrelated")

    adapter._client.aio.models.generate_content = _raise

    with pytest.raises(Exception) as exc_info:
        await adapter.complete(
            CompletionRequest(model="gemini-2.5-flash", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
        )

    assert exc_info.value.kind == ProviderErrorKind.UNKNOWN
