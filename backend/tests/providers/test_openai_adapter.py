"""OpenAI (Responses API) adapter tests. No network access, no API key --
shapes verified directly from the installed openai==3.17.0 package (see
openai_adapter.py's module docstring for sources). Fixture testing, not
live testing -- see docs/PROVIDER_NOTES.md.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from app.providers.contracts import (
    CompletionRequest,
    DoneEvent,
    ErrorEvent,
    FinishReason,
    TextDeltaEvent,
    ToolUseCompleteEvent,
    ToolUseDeltaEvent,
    ToolUseStartEvent,
    UsageEvent,
)
from app.providers.errors import ProviderErrorKind
from app.providers.messages import Message, Role, TextBlock, ToolDefinition, ToolResultBlock, ToolUseBlock
from app.providers.openai_adapter import OpenAIAdapter


@pytest.fixture
def adapter() -> OpenAIAdapter:
    return OpenAIAdapter(api_key="test-key")


def _item(**kwargs):
    return SimpleNamespace(**kwargs)


def _http_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return httpx.Response(status_code, request=request, json={"error": {"message": "x", "type": "x"}})


# --- Request translation ----------------------------------------------------


def test_translates_system_prompt_to_instructions(adapter):
    request = CompletionRequest(
        model="gpt-5.6-terra", system="Be concise.", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100
    )
    payload = adapter._build_request(request)
    assert payload["instructions"] == "Be concise."


def test_translates_history_into_message_items():
    request = CompletionRequest(
        model="gpt-5.6-terra",
        messages=[
            Message(role=Role.USER, content=[TextBlock(text="What's 2+2?")]),
            Message(role=Role.ASSISTANT, content=[TextBlock(text="4")]),
        ],
        max_tokens=100,
    )
    payload = OpenAIAdapter(api_key="k")._build_request(request)
    assert payload["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "What's 2+2?"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "input_text", "text": "4"}]},
    ]


def test_translates_assistant_tool_use_into_function_call_item():
    request = CompletionRequest(
        model="gpt-5.6-terra",
        messages=[
            Message(role=Role.ASSISTANT, content=[ToolUseBlock(id="call_1", name="get_weather", input={"location": "Boston"})]),
        ],
        max_tokens=100,
    )
    payload = OpenAIAdapter(api_key="k")._build_request(request)
    assert payload["input"] == [
        {"type": "function_call", "call_id": "call_1", "name": "get_weather", "arguments": '{"location": "Boston"}'}
    ]


def test_translates_tool_role_into_function_call_output_item():
    request = CompletionRequest(
        model="gpt-5.6-terra",
        messages=[Message(role=Role.TOOL, content=[ToolResultBlock(tool_use_id="call_1", content="15C and cloudy")])],
        max_tokens=100,
    )
    payload = OpenAIAdapter(api_key="k")._build_request(request)
    assert payload["input"] == [{"type": "function_call_output", "call_id": "call_1", "output": "15C and cloudy"}]


def test_translates_max_tokens_and_temperature():
    request = CompletionRequest(
        model="gpt-5.6-terra", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=512, temperature=0.3
    )
    payload = OpenAIAdapter(api_key="k")._build_request(request)
    assert payload["max_output_tokens"] == 512
    assert payload["temperature"] == 0.3


def test_translates_tool_definitions_to_function_tool_schema():
    request = CompletionRequest(
        model="gpt-5.6-terra",
        messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])],
        tools=[ToolDefinition(name="get_weather", description="Get weather", input_schema={"type": "object"})],
        max_tokens=100,
    )
    payload = OpenAIAdapter(api_key="k")._build_request(request)
    assert payload["tools"] == [
        {"type": "function", "name": "get_weather", "description": "Get weather", "parameters": {"type": "object"}, "strict": None}
    ]


# --- Response normalization (complete) --------------------------------------


async def test_complete_normalizes_text_usage_and_finish_reason(adapter):
    fake_response = SimpleNamespace(
        output=[_item(type="message", content=[_item(type="output_text", text="Hello there")])],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
            output_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
        status="completed",
        incomplete_details=None,
    )
    adapter._client.responses.create = AsyncMock(return_value=fake_response)

    response = await adapter.complete(
        CompletionRequest(model="gpt-5.6-terra", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
    )

    assert response.content == [TextBlock(text="Hello there")]
    assert response.usage.input_tokens == 10
    assert response.usage.reasoning_tokens == 0  # reported as zero, not "not reported"
    assert response.finish_reason == FinishReason.STOP


async def test_complete_normalizes_tool_use_and_tool_use_finish_reason(adapter):
    fake_response = SimpleNamespace(
        output=[_item(type="function_call", call_id="call_1", name="get_weather", arguments='{"location": "Boston"}')],
        usage=SimpleNamespace(
            input_tokens=20,
            output_tokens=8,
            input_tokens_details=SimpleNamespace(cached_tokens=4),
            output_tokens_details=SimpleNamespace(reasoning_tokens=None),
        ),
        status="completed",
        incomplete_details=None,
    )
    adapter._client.responses.create = AsyncMock(return_value=fake_response)

    response = await adapter.complete(
        CompletionRequest(model="gpt-5.6-terra", messages=[Message(role=Role.USER, content=[TextBlock(text="weather?")])], max_tokens=100)
    )

    assert response.content == [ToolUseBlock(id="call_1", name="get_weather", input={"location": "Boston"})]
    assert response.usage.cached_input_tokens == 4
    assert response.finish_reason == FinishReason.TOOL_USE


async def test_complete_normalizes_incomplete_content_filter_response(adapter):
    fake_response = SimpleNamespace(
        output=[_item(type="message", content=[])],
        usage=None,
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="content_filter"),
    )
    adapter._client.responses.create = AsyncMock(return_value=fake_response)

    response = await adapter.complete(
        CompletionRequest(model="gpt-5.6-terra", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
    )

    assert response.finish_reason == FinishReason.CONTENT_FILTER
    assert response.usage.input_tokens is None  # usage was None -- "not reported", not 0


# --- Streaming ----------------------------------------------------------------


class _AsyncEventIterable:
    def __init__(self, events):
        self._events = events

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for event in self._events:
            yield event


def _event(type_, **kwargs):
    return SimpleNamespace(type=type_, **kwargs)


async def test_stream_normalizes_text_deltas(adapter):
    final_response = SimpleNamespace(
        output=[_item(type="message", content=[_item(type="output_text", text="Hello")])],
        usage=SimpleNamespace(
            input_tokens=5, output_tokens=2, input_tokens_details=SimpleNamespace(cached_tokens=0), output_tokens_details=SimpleNamespace(reasoning_tokens=0)
        ),
        status="completed",
        incomplete_details=None,
    )
    events = [
        _event("response.output_text.delta", delta="Hel"),
        _event("response.output_text.delta", delta="lo"),
        _event("response.completed", response=final_response),
    ]
    adapter._client.responses.create = AsyncMock(return_value=_AsyncEventIterable(events))

    request = CompletionRequest(model="gpt-5.6-terra", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
    collected = [e async for e in adapter.stream(request)]

    text_events = [e for e in collected if isinstance(e, TextDeltaEvent)]
    assert [e.text for e in text_events] == ["Hel", "lo"]
    assert isinstance(collected[-1], DoneEvent)
    assert collected[-1].finish_reason == FinishReason.STOP


async def test_stream_correlates_function_call_arguments_by_item_id_not_call_id(adapter):
    # item.id (internal) and item.call_id (the correlator sent back in
    # function_call_output) are DIFFERENT strings on this provider -- the
    # delta/done events only carry item_id, so the adapter must translate.
    added_item = _item(type="function_call", id="item_abc", call_id="call_1", name="get_weather")
    final_response = SimpleNamespace(
        output=[_item(type="function_call", call_id="call_1", name="get_weather", arguments='{"city": "Boston"}')],
        usage=SimpleNamespace(
            input_tokens=5, output_tokens=9, input_tokens_details=SimpleNamespace(cached_tokens=0), output_tokens_details=SimpleNamespace(reasoning_tokens=0)
        ),
        status="completed",
        incomplete_details=None,
    )
    events = [
        _event("response.output_item.added", item=added_item, output_index=0),
        _event("response.function_call_arguments.delta", item_id="item_abc", delta='{"city":'),
        _event("response.function_call_arguments.delta", item_id="item_abc", delta=' "Boston"}'),
        _event("response.function_call_arguments.done", item_id="item_abc", arguments='{"city": "Boston"}'),
        _event("response.completed", response=final_response),
    ]
    adapter._client.responses.create = AsyncMock(return_value=_AsyncEventIterable(events))

    request = CompletionRequest(model="gpt-5.6-terra", messages=[Message(role=Role.USER, content=[TextBlock(text="weather?")])], max_tokens=100)
    collected = [e async for e in adapter.stream(request)]

    start = next(e for e in collected if isinstance(e, ToolUseStartEvent))
    assert start.id == "call_1"  # the call_id, not the internal item id

    deltas = [e for e in collected if isinstance(e, ToolUseDeltaEvent)]
    assert [d.partial_json for d in deltas] == ['{"city":', ' "Boston"}']
    assert all(d.id == "call_1" for d in deltas)

    complete = next(e for e in collected if isinstance(e, ToolUseCompleteEvent))
    assert complete.id == "call_1"
    assert complete.input == {"city": "Boston"}


async def test_stream_yields_error_event_instead_of_raising(adapter):
    events = [_event("error", code="rate_limit_exceeded", message="slow down", param=None)]
    adapter._client.responses.create = AsyncMock(return_value=_AsyncEventIterable(events))

    request = CompletionRequest(model="gpt-5.6-terra", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
    collected = [e async for e in adapter.stream(request)]

    assert len(collected) == 1
    assert isinstance(collected[0], ErrorEvent)
    assert collected[0].kind == ProviderErrorKind.RATE_LIMIT


# --- Error normalization ------------------------------------------------------


@pytest.mark.parametrize(
    ("exc_factory", "expected_kind"),
    [
        (lambda: openai.AuthenticationError("bad key", response=_http_response(401), body=None), ProviderErrorKind.AUTH),
        (lambda: openai.RateLimitError("slow down", response=_http_response(429), body=None), ProviderErrorKind.RATE_LIMIT),
        (lambda: openai.BadRequestError("bad", response=_http_response(400), body=None), ProviderErrorKind.BAD_REQUEST),
        (
            lambda: openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses")),
            ProviderErrorKind.TIMEOUT,
        ),
        (lambda: openai.InternalServerError("oops", response=_http_response(500), body=None), ProviderErrorKind.SERVER_ERROR),
        (lambda: RuntimeError("something unrelated"), ProviderErrorKind.UNKNOWN),
    ],
)
async def test_complete_translates_native_errors_to_normalized_kinds(adapter, exc_factory, expected_kind):
    async def _raise(**kwargs):
        raise exc_factory()

    adapter._client.responses.create = _raise

    with pytest.raises(Exception) as exc_info:
        await adapter.complete(
            CompletionRequest(model="gpt-5.6-terra", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
        )

    assert exc_info.value.kind == expected_kind
    assert exc_info.value.provider == "openai"
