"""Deliberate context-window handling.

Strategy (see docs/DESIGN.md, "Context Window Management" for the full
writeup): keep the newest messages, drop the oldest ones, until the
estimated token count fits the model's context window minus a reserved
output budget and a safety margin. No summarization subsystem -- for a
one-day take-home, a deterministic drop-the-oldest strategy is the
defensible choice CP-00 called for, not a build-it-if-there's-time
extra.

**Token counts here are estimates, not exact.** None of the three
configured providers' official Python SDKs expose a free, universal,
pre-request tokenizer usable across all three without an extra
per-provider dependency (Anthropic's own token-counting is a separate API
call; OpenAI's `tiktoken` doesn't model Gemini's tokenizer at all). Rather
than add three different tokenizers for a rough estimate, this uses the
well-known ~4-characters-per-token heuristic (documented as approximate by
every provider that mentions it) with a safety margin on top, and is
explicit about being an approximation rather than pretending otherwise.
"""

from app.providers.messages import ImageBlock, Message, TextBlock, ToolResultBlock, ToolUseBlock

# Rough, provider-agnostic estimate: ~4 characters per token in English
# text. This is a well-known approximation, not a claim of exactness.
_CHARS_PER_TOKEN_ESTIMATE = 4

# Applied on top of (context_window - reserved_output_tokens) so the
# estimate's inherent inaccuracy doesn't itself cause an overflow.
_SAFETY_MARGIN = 0.9


def estimate_tokens(message: Message) -> int:
    total_chars = 0
    for block in message.content:
        if isinstance(block, TextBlock):
            total_chars += len(block.text)
        elif isinstance(block, ToolResultBlock):
            total_chars += len(block.content)
        elif isinstance(block, ToolUseBlock):
            total_chars += len(block.name) + len(str(block.input))
        elif isinstance(block, ImageBlock):
            # Base64 image bytes aren't remotely 4-chars-per-token text;
            # a fixed placeholder is a coarser but honest stand-in rather
            # than wildly overcounting raw base64 length as "tokens".
            total_chars += 1000 * _CHARS_PER_TOKEN_ESTIMATE
    return max(1, total_chars // _CHARS_PER_TOKEN_ESTIMATE)


def trim_to_context_window(
    messages: list[Message],
    *,
    context_window: int,
    reserved_output_tokens: int,
) -> list[Message]:
    """Keeps the newest messages that fit; drops the oldest first.

    Always returns at least the single newest message, even if its own
    estimate alone exceeds the budget -- CP-04 doesn't truncate the
    content *within* one message, only decides which whole messages to
    include. Sending one over-budget message and letting the provider
    reject it with a real ``context_length`` error is preferable to
    silently returning an empty conversation.
    """
    if not messages:
        return []

    budget = max(1, int((context_window - reserved_output_tokens) * _SAFETY_MARGIN))

    kept: list[Message] = []
    used = 0
    for message in reversed(messages):
        cost = estimate_tokens(message)
        if kept and used + cost > budget:
            break
        kept.append(message)
        used += cost

    kept.reverse()
    return kept
