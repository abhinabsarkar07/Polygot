"""Proves CompletionRequest, CompletionResponse, StreamEvent, Usage, and
FinishReason can represent everything the assignment requires."""

import pytest
from pydantic import TypeAdapter, ValidationError

from app.providers import (
    CompletionRequest,
    CompletionResponse,
    DoneEvent,
    ErrorEvent,
    FinishReason,
    Message,
    ProviderErrorKind,
    Role,
    StreamEvent,
    TextBlock,
    TextDeltaEvent,
    ToolDefinition,
    ToolUseCompleteEvent,
    ToolUseDeltaEvent,
    ToolUseStartEvent,
    Usage,
    UsageEvent,
)

stream_event_adapter: TypeAdapter[StreamEvent] = TypeAdapter(StreamEvent)


def test_completion_request_supports_model_messages_system_tools_and_generation_settings():
    request = CompletionRequest(
        model="claude-sonnet",
        system="You are a helpful assistant.",
        messages=[Message(role=Role.USER, content=[TextBlock(text="hi")])],
        tools=[ToolDefinition(name="get_weather", description="...", input_schema={"type": "object"})],
        max_tokens=1024,
        temperature=0.7,
    )
    assert request.model == "claude-sonnet"
    assert request.system == "You are a helpful assistant."
    assert request.tools[0].name == "get_weather"


def test_completion_request_requires_model_and_max_tokens():
    with pytest.raises(ValidationError):
        CompletionRequest(messages=[])


def test_completion_response_carries_usage_and_finish_reason():
    response = CompletionResponse(
        model="claude-sonnet",
        content=[TextBlock(text="hello back")],
        usage=Usage(input_tokens=10, output_tokens=5),
        finish_reason=FinishReason.STOP,
    )
    assert response.finish_reason == FinishReason.STOP
    assert response.usage.input_tokens == 10


@pytest.mark.parametrize(
    "event",
    [
        TextDeltaEvent(text="hel"),
        ToolUseStartEvent(id="call_1", name="get_weather"),
        ToolUseDeltaEvent(id="call_1", partial_json='{"loc'),
        ToolUseCompleteEvent(id="call_1", name="get_weather", input={"location": "Berlin"}),
        UsageEvent(usage=Usage(input_tokens=10, output_tokens=None)),
        DoneEvent(finish_reason=FinishReason.STOP),
        ErrorEvent(kind=ProviderErrorKind.RATE_LIMIT, message="slow down"),
    ],
)
def test_every_required_stream_event_kind_round_trips_through_the_discriminated_union(event):
    # Round-tripping through the union's own adapter (not the concrete
    # class) is the real test: it's what an SSE endpoint will do with each
    # event before it's serialized onto the wire.
    parsed = stream_event_adapter.validate_python(event.model_dump())
    assert parsed == event


def test_usage_distinguishes_not_reported_from_reported_as_zero():
    not_reported = Usage(input_tokens=100, output_tokens=50)
    reported_zero = Usage(input_tokens=100, output_tokens=50, reasoning_tokens=0)

    assert not_reported.reasoning_tokens is None
    assert reported_zero.reasoning_tokens == 0
    assert not_reported.reasoning_tokens != reported_zero.reasoning_tokens


def test_finish_reason_values_are_normalized_not_provider_strings():
    assert {r.value for r in FinishReason} == {
        "stop",
        "length",
        "tool_use",
        "content_filter",
        "error",
        "unknown",
    }
