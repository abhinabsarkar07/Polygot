"""Anthropic adapter tests. No network access, no API key -- the SDK
client's methods are replaced with fakes returning shapes verified
directly from the installed anthropic==1.7.0 package (see
anthropic_adapter.py's module docstring for sources). This is fixture
testing, not live testing -- see docs/PROVIDER_NOTES.md for which
providers were actually exercised against a real API key.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import httpx
import pytest

from app.providers.anthropic_adapter import AnthropicAdapter
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


@pytest.fixture
def adapter() -> AnthropicAdapter:
    return AnthropicAdapter(api_key="test-key")


def _block(**kwargs):
    return SimpleNamespace(**kwargs)


def _http_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status_code, request=request, json={"error": {"type": "x", "message": "x"}})


# --- Request translation ----------------------------------------------------


def test_translates_system_prompt_to_top_level_field(adapter):
    request = CompletionRequest(
        model="claude-sonnet-5",
        system="Be concise.",
        messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])],
        max_tokens=100,
    )
    payload = adapter._build_request(request)
    assert payload["system"] == "Be concise."


def test_translates_user_text_and_assistant_history():
    request = CompletionRequest(
        model="claude-sonnet-5",
        messages=[
            Message(role=Role.USER, content=[TextBlock(text="What's 2+2?")]),
            Message(role=Role.ASSISTANT, content=[TextBlock(text="4")]),
            Message(role=Role.USER, content=[TextBlock(text="And 3+3?")]),
        ],
        max_tokens=100,
    )
    payload = AnthropicAdapter(api_key="k")._build_request(request)
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant", "user"]
    assert payload["messages"][1]["content"] == [{"type": "text", "text": "4"}]


def test_translates_tool_role_to_user_with_tool_result_block():
    request = CompletionRequest(
        model="claude-sonnet-5",
        messages=[
            Message(role=Role.ASSISTANT, content=[ToolUseBlock(id="call_1", name="get_weather", input={"location": "Boston"})]),
            Message(role=Role.TOOL, content=[ToolResultBlock(tool_use_id="call_1", content="15C and cloudy")]),
        ],
        max_tokens=100,
    )
    payload = AnthropicAdapter(api_key="k")._build_request(request)
    tool_message = payload["messages"][1]
    assert tool_message["role"] == "user"
    assert tool_message["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "15C and cloudy", "is_error": False}
    ]


def test_translates_max_tokens_and_temperature():
    request = CompletionRequest(
        model="claude-sonnet-5",
        messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])],
        max_tokens=512,
        temperature=0.3,
    )
    payload = AnthropicAdapter(api_key="k")._build_request(request)
    assert payload["max_tokens"] == 512
    assert payload["temperature"] == 0.3


def test_translates_tool_definitions_to_anthropic_tool_schema():
    request = CompletionRequest(
        model="claude-sonnet-5",
        messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])],
        tools=[ToolDefinition(name="get_weather", description="Get weather", input_schema={"type": "object"})],
        max_tokens=100,
    )
    payload = AnthropicAdapter(api_key="k")._build_request(request)
    assert payload["tools"] == [{"name": "get_weather", "description": "Get weather", "input_schema": {"type": "object"}}]


# --- Response normalization (complete) --------------------------------------


async def test_complete_normalizes_text_usage_and_finish_reason(adapter):
    fake_message = SimpleNamespace(
        content=[_block(type="text", text="Hello there")],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=None),
        stop_reason="end_turn",
    )
    adapter._client.messages.create = AsyncMock(return_value=fake_message)

    response = await adapter.complete(
        CompletionRequest(model="claude-sonnet-5", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
    )

    assert response.content == [TextBlock(text="Hello there")]
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 5
    assert response.usage.reasoning_tokens is None
    assert response.finish_reason == FinishReason.STOP


async def test_complete_normalizes_tool_use_and_tool_use_finish_reason(adapter):
    fake_message = SimpleNamespace(
        content=[_block(type="tool_use", id="toolu_1", name="get_weather", input={"location": "Boston"})],
        usage=SimpleNamespace(input_tokens=20, output_tokens=8, cache_read_input_tokens=4),
        stop_reason="tool_use",
    )
    adapter._client.messages.create = AsyncMock(return_value=fake_message)

    response = await adapter.complete(
        CompletionRequest(model="claude-sonnet-5", messages=[Message(role=Role.USER, content=[TextBlock(text="weather?")])], max_tokens=100)
    )

    assert response.content == [ToolUseBlock(id="toolu_1", name="get_weather", input={"location": "Boston"})]
    assert response.usage.cached_input_tokens == 4
    assert response.finish_reason == FinishReason.TOOL_USE


# --- Streaming ----------------------------------------------------------------


class _FakeStreamContext:
    def __init__(self, events):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def __aiter__(self):
        return self._aiter()

    async def _aiter(self):
        for event in self._events:
            yield event


def _sse(type_, **kwargs):
    return SimpleNamespace(type=type_, **kwargs)


async def test_stream_normalizes_text_deltas(adapter):
    events = [
        _sse("message_start", message=SimpleNamespace(usage=SimpleNamespace(input_tokens=5))),
        _sse("content_block_start", index=0, content_block=_block(type="text")),
        _sse("content_block_delta", index=0, delta=_block(type="text_delta", text="Hel")),
        _sse("content_block_delta", index=0, delta=_block(type="text_delta", text="lo")),
        _sse("content_block_stop", index=0),
        _sse("message_delta", delta=SimpleNamespace(stop_reason="end_turn"), usage=SimpleNamespace(output_tokens=2)),
        _sse("message_stop"),
    ]
    adapter._client.messages.stream = lambda **kwargs: _FakeStreamContext(events)

    request = CompletionRequest(model="claude-sonnet-5", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
    collected = [e async for e in adapter.stream(request)]

    text_events = [e for e in collected if isinstance(e, TextDeltaEvent)]
    assert [e.text for e in text_events] == ["Hel", "lo"]
    assert isinstance(collected[-1], DoneEvent)
    assert collected[-1].finish_reason == FinishReason.STOP


async def test_stream_does_not_parse_fragmented_tool_json_until_block_stop(adapter):
    # Mirrors the assignment's exact fragmentation example: `{"city":` then
    # `"Boston"}` must never be parsed as standalone JSON mid-stream.
    events = [
        _sse("message_start", message=SimpleNamespace(usage=SimpleNamespace(input_tokens=5))),
        _sse("content_block_start", index=0, content_block=_block(type="tool_use", id="toolu_1", name="get_weather")),
        _sse("content_block_delta", index=0, delta=_block(type="input_json_delta", partial_json='{"city":')),
        _sse("content_block_delta", index=0, delta=_block(type="input_json_delta", partial_json=' "Boston"}')),
        _sse("content_block_stop", index=0),
        _sse("message_delta", delta=SimpleNamespace(stop_reason="tool_use"), usage=SimpleNamespace(output_tokens=9)),
        _sse("message_stop"),
    ]
    adapter._client.messages.stream = lambda **kwargs: _FakeStreamContext(events)

    request = CompletionRequest(model="claude-sonnet-5", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
    collected = [e async for e in adapter.stream(request)]

    start = next(e for e in collected if isinstance(e, ToolUseStartEvent))
    assert start.id == "toolu_1"
    assert start.name == "get_weather"

    deltas = [e for e in collected if isinstance(e, ToolUseDeltaEvent)]
    assert [d.partial_json for d in deltas] == ['{"city":', ' "Boston"}']
    # Each fragment is exactly what the provider sent -- neither one is
    # valid JSON alone, and nothing in the adapter tries to parse them yet.

    complete = next(e for e in collected if isinstance(e, ToolUseCompleteEvent))
    assert complete.id == "toolu_1"
    assert complete.input == {"city": "Boston"}

    usage_events = [e for e in collected if isinstance(e, UsageEvent)]
    assert usage_events[-1].usage.input_tokens == 5
    assert usage_events[-1].usage.output_tokens == 9


async def test_stream_yields_error_event_instead_of_raising(adapter):
    class _FailingContext:
        async def __aenter__(self):
            raise anthropic.RateLimitError("slow down", response=_http_response(429), body=None)

        async def __aexit__(self, *exc_info):
            return False

    adapter._client.messages.stream = lambda **kwargs: _FailingContext()

    request = CompletionRequest(model="claude-sonnet-5", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
    collected = [e async for e in adapter.stream(request)]

    assert len(collected) == 1
    assert isinstance(collected[0], ErrorEvent)
    assert collected[0].kind == ProviderErrorKind.RATE_LIMIT


# --- Cancellation ---------------------------------------------------------------
#
# Representative of all three adapters, not just this one: each wraps its
# streaming loop in `except Exception`, never `except BaseException` or a
# bare `except:`. Since Python 3.8, asyncio.CancelledError subclasses
# BaseException specifically so broad exception handlers like this one
# don't accidentally swallow task cancellation -- this test proves that
# property holds through this adapter's actual code, not just in the
# abstract.


async def test_cancelling_the_consuming_task_propagates_not_swallowed_as_an_error_event(adapter):
    import asyncio

    class _HangingContext:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(3600)  # never resolves on its own; only cancellation ends this

    adapter._client.messages.stream = lambda **kwargs: _HangingContext()
    request = CompletionRequest(model="claude-sonnet-5", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)

    async def _consume():
        async for _ in adapter.stream(request):
            pass

    task = asyncio.ensure_future(_consume())
    await asyncio.sleep(0)  # let the task actually start awaiting inside the adapter
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


# --- Error normalization ------------------------------------------------------


@pytest.mark.parametrize(
    ("exc_factory", "expected_kind"),
    [
        (lambda: anthropic.AuthenticationError("bad key", response=_http_response(401), body=None), ProviderErrorKind.AUTH),
        (lambda: anthropic.RateLimitError("slow down", response=_http_response(429), body=None), ProviderErrorKind.RATE_LIMIT),
        (
            lambda: anthropic.RequestTooLargeError("too big", response=_http_response(413), body=None),
            ProviderErrorKind.CONTEXT_LENGTH,
        ),
        (lambda: anthropic.BadRequestError("bad", response=_http_response(400), body=None), ProviderErrorKind.BAD_REQUEST),
        (lambda: anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")), ProviderErrorKind.TIMEOUT),
        (
            lambda: anthropic.InternalServerError("oops", response=_http_response(500), body=None),
            ProviderErrorKind.SERVER_ERROR,
        ),
        (lambda: RuntimeError("something unrelated"), ProviderErrorKind.UNKNOWN),
    ],
)
async def test_complete_translates_native_errors_to_normalized_kinds(adapter, exc_factory, expected_kind):
    async def _raise(**kwargs):
        raise exc_factory()

    adapter._client.messages.create = _raise

    with pytest.raises(Exception) as exc_info:
        await adapter.complete(
            CompletionRequest(model="claude-sonnet-5", messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])], max_tokens=100)
        )

    assert exc_info.value.kind == expected_kind
    assert exc_info.value.provider == "anthropic"
