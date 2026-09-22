"""Proves the normalized message/content-block model can represent every
shape the assignment requires: plain text, tool use, and tool results,
without any provider-specific field."""

import pytest
from pydantic import ValidationError

from app.providers import (
    ImageBlock,
    Message,
    Role,
    TextBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolUseBlock,
)


def test_message_can_represent_plain_text():
    message = Message(role=Role.USER, content=[TextBlock(text="What is this?")])
    assert message.content[0].text == "What is this?"


def test_message_can_represent_assistant_text_plus_tool_use():
    message = Message(
        role=Role.ASSISTANT,
        content=[
            TextBlock(text="Let me check the weather."),
            ToolUseBlock(id="call_1", name="get_weather", input={"location": "Berlin"}),
        ],
    )
    assert message.content[0].type == "text"
    assert message.content[1].type == "tool_use"
    assert message.content[1].input == {"location": "Berlin"}


def test_message_can_represent_tool_result():
    message = Message(
        role=Role.TOOL,
        content=[ToolResultBlock(tool_use_id="call_1", content="15C and cloudy")],
    )
    block = message.content[0]
    assert block.type == "tool_result"
    assert block.tool_use_id == "call_1"
    assert block.is_error is False


def test_message_can_represent_image():
    message = Message(
        role=Role.USER,
        content=[ImageBlock(media_type="image/png", data="aGVsbG8=")],
    )
    assert message.content[0].type == "image"


def test_content_block_discriminator_rejects_unknown_type():
    with pytest.raises(ValidationError):
        Message.model_validate({"role": "user", "content": [{"type": "audio", "data": "x"}]})


def test_tool_definition_carries_json_schema():
    tool = ToolDefinition(
        name="get_weather",
        description="Get current weather for a location",
        input_schema={
            "type": "object",
            "properties": {"location": {"type": "string"}},
            "required": ["location"],
        },
    )
    assert tool.input_schema["required"] == ["location"]
