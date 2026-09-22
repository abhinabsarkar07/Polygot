"""Deliberate, deterministic context-window trimming -- no summarization,
no silently-unbounded history. See app/services/context_window.py.
"""

from app.providers.messages import Message, Role, TextBlock
from app.services.context_window import trim_to_context_window


def _msg(role: Role, text: str) -> Message:
    return Message(role=role, content=[TextBlock(text=text)])


def test_all_messages_kept_when_well_under_budget():
    messages = [_msg(Role.USER, "hi"), _msg(Role.ASSISTANT, "hello"), _msg(Role.USER, "how are you?")]
    trimmed = trim_to_context_window(messages, context_window=1_000_000, reserved_output_tokens=4096)
    assert trimmed == messages


def test_oldest_messages_dropped_first_when_over_budget():
    # Each message is ~250 chars ~= 62 tokens at the 4-chars-per-token
    # estimate. A tiny context window forces dropping the oldest ones.
    messages = [_msg(Role.USER, f"message number {i} " + "x" * 230) for i in range(10)]
    trimmed = trim_to_context_window(messages, context_window=300, reserved_output_tokens=0)

    assert trimmed == messages[-len(trimmed) :]  # a contiguous suffix -- newest kept, oldest dropped
    assert len(trimmed) < len(messages)
    assert trimmed[-1] == messages[-1]  # the newest message is always present


def test_reserved_output_tokens_shrinks_the_usable_budget():
    messages = [_msg(Role.USER, "x" * 200)]
    generous = trim_to_context_window(messages, context_window=1000, reserved_output_tokens=0)
    stingy = trim_to_context_window(messages, context_window=1000, reserved_output_tokens=950)
    assert generous == messages
    # Still returns the single newest message even though it doesn't fit --
    # see the function's own docstring: CP-04 doesn't truncate mid-message.
    assert stingy == messages


def test_newest_single_message_is_always_kept_even_if_it_alone_exceeds_budget():
    huge = [_msg(Role.USER, "x" * 100_000)]
    trimmed = trim_to_context_window(huge, context_window=10, reserved_output_tokens=0)
    assert trimmed == huge


def test_empty_history_returns_empty():
    assert trim_to_context_window([], context_window=1000, reserved_output_tokens=0) == []
