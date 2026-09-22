# Provider Notes

Concrete, sourced facts about each provider's API and this project's
adapters (`app/providers/anthropic_adapter.py`, `gemini_adapter.py`,
`openai_adapter.py`). Everything below was verified on **2026-09-22**
against the installed SDK's own source (the most reliable source available
-- it's the exact code this project runs) and/or official first-party
docs, not blog posts or memory. Model IDs and pricing were additionally
verified in CP-02; see the "Pricing" sections below for sources.

**Live- vs fixture-tested:** all three adapters' own unit tests
(`tests/providers/test_{anthropic,openai,gemini}_adapter.py`) remain
**fixture-tested only** -- SDK client methods replaced with fakes, no
network call, no API key required to run the suite. As of CP-04, however,
**Anthropic has been live-tested end-to-end** through the real
application (a real key, added and verified during CP-04's manual
verification step): real non-streaming auth failure, a real streaming
completion (`claude-sonnet` -> `claude-sonnet-5`, tokens genuinely
streamed and rendered incrementally), and a real mid-generation
cancellation, all against the live API -- see `docs/AI_USAGE.md`'s CP-04
section for the three real bugs that live pass surfaced (none of them
were in the adapter itself; see below). Gemini and OpenAI remain
fixture-tested only -- no key was available to verify them live.

---

# Anthropic

## SDK / API Used

Official `anthropic` Python SDK, pinned `anthropic==1.7.0`. Messages API
(`client.messages.create` / `client.messages.stream`) -- Anthropic has one
completion API, unlike OpenAI's two.

## Configured Model

`claude-sonnet` (internal id) -> `claude-sonnet-5` (provider model id).
1,000,000 token context window, 128,000 max output tokens.

## System Prompt Handling

Top-level `system` string parameter on the request -- not a message with a
system role. `CompletionRequest.system` maps directly to it.

## Message Format

`messages: [{"role": "user"|"assistant", "content": [block, ...]}]`. Only
two roles exist on the wire. Our `Role.TOOL` has no equivalent -- a tool
result is sent back as a `tool_result` content block inside a **user**
message immediately following the assistant message that made the call.
`AnthropicAdapter._translate_message` collapses `Role.TOOL` into `"user"`.

Content blocks: `{"type": "text", "text": ...}`,
`{"type": "image", "source": {"type": "base64", "media_type": ..., "data": ...}}`,
`{"type": "tool_use", "id", "name", "input"}`,
`{"type": "tool_result", "tool_use_id", "content", "is_error"}` -- this
last one is a near-exact match for our `ToolResultBlock`, unlike OpenAI's
equivalent (see below).

## Streaming

Verified against https://platform.claude.com/docs/en/build-with-claude/streaming
(fetched directly, full event examples). SSE event sequence:
`message_start` -> repeating (`content_block_start`, one or more
`content_block_delta`, `content_block_stop`) per content block ->
one or more `message_delta` -> `message_stop`, with `ping` events
interspersed (ignored).

Usage arrives in two places: `message_start.message.usage.input_tokens`
(the input count, once) and `message_delta.usage.output_tokens`
(**cumulative** output count, repeated on every `message_delta`). The
adapter captures input once and re-emits a `UsageEvent` on every
`message_delta`.

## Tool Definitions

`{"name", "description", "input_schema"}` -- **identical in shape** to our
`ToolDefinition`. This is the only one of the three providers where tool
translation is a pure passthrough with no restructuring.

## Tool Calls

A `tool_use` content block: `{"type": "tool_use", "id", "name", "input": {}}`
(the `input` object starts empty in the streaming `content_block_start`
event and is filled in by subsequent deltas).

## Tool Argument Streaming

Confirmed from the docs' own SSE examples: arguments stream as
`content_block_delta` events with `delta.type == "input_json_delta"` and a
`partial_json` string fragment -- e.g. `{"location":` then ` "San
Fra` then `ncisco, CA"}`, deliberately **not** valid JSON at each
intermediate step ("the deltas are *partial JSON strings*... You can
accumulate the string deltas and parse the JSON once you receive a
`content_block_stop` event" -- direct quote from the docs). The adapter
accumulates fragments per content-block index and only calls `json.loads`
once `content_block_stop` fires for that index. Tested explicitly in
`test_stream_does_not_parse_fragmented_tool_json_until_block_stop`, using
the assignment's own `{"city":` / `"Boston"}` example.

## Usage Reporting

`input_tokens`, `output_tokens`, optionally `cache_read_input_tokens` /
`cache_creation_input_tokens`. **No separate reasoning-token metric** --
extended-thinking tokens are billed as part of `output_tokens`, not broken
out. `Usage.reasoning_tokens` is always `None` for this adapter (not `0`)
-- "not reported", not "reported as zero".

## Finish Reasons

Verified against https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons
(fetched directly -- full enumerated list, not inferred):

| Anthropic `stop_reason` | Normalized `FinishReason` |
| --- | --- |
| `end_turn` | `STOP` |
| `stop_sequence` | `STOP` |
| `max_tokens` | `LENGTH` |
| `model_context_window_exceeded` | `LENGTH` |
| `tool_use` | `TOOL_USE` |
| `refusal` | `CONTENT_FILTER` |
| `pause_turn` | `UNKNOWN` (server-tool sampling-loop pause; no normalized equivalent) |

**Content-policy rejection is a `stop_reason` on a normal HTTP 200
response**, not a raised exception -- see Errors, below.

## Errors

Verified directly against the installed SDK's own `anthropic._exceptions`
module (more reliable than the docs page, which only names the pattern
generically) -- see `AnthropicAdapter._translate_error`:

| Exception class | HTTP status | Normalized kind |
| --- | --- | --- |
| `AuthenticationError`, `PermissionDeniedError` | 401, 403 | `AUTH` |
| `RateLimitError` | 429 | `RATE_LIMIT` |
| `RequestTooLargeError` | 413 | `CONTEXT_LENGTH` |
| `APITimeoutError`, `DeadlineExceededError` | -- / 504 | `TIMEOUT` |
| `BadRequestError`, `NotFoundError`, `ConflictError`, `UnprocessableEntityError` | 400, 404, 409, 422 | `BAD_REQUEST` |
| `APIStatusError`, `APIConnectionError` (covers `OverloadedError` 529, `ServiceUnavailableError` 503, `InternalServerError`) | 5xx / connection-level | `SERVER_ERROR` |
| anything else | -- | `UNKNOWN` |

**No `CONTENT_FILTER` ProviderError exists for this provider in practice**
-- content policy surfaces via `stop_reason: "refusal"` on a successful
response (mapped to `FinishReason.CONTENT_FILTER`), never as a raised
exception. See "Important Quirks".

## Cancellation

Live-tested in CP-04: a real streaming generation was cancelled
mid-response (via a client disconnect through the full app, not just at
the adapter level), and the upstream Anthropic connection genuinely
stopped -- confirmed by the accumulated text staying at a single
character rather than continuing to grow after the disconnect. The
adapter itself needed no changes for this; the bug CP-04 found and fixed
was one level up, in how the *application* persisted the interrupted
result under real cancellation semantics (Starlette's anyio cancel scope
vs. plain `asyncio.Task.cancel()`) -- see `docs/DESIGN.md`, "Cancellation".
By construction: `stream()`'s `except Exception` clause does not catch
`asyncio.CancelledError` (a `BaseException` subclass since Python 3.8), so
cancelling the consuming task propagates rather than being swallowed or
converted into an `ErrorEvent`. Verified in
`test_cancelling_the_consuming_task_propagates_not_swallowed_as_an_error_event`.

## Pricing

Verified 2026-09-22 against https://platform.claude.com/docs/en/about-claude/pricing
(official docs, fetched directly).

| | Input | Output | Cache write (5m / 1h) | Cache read (hit) |
| --- | --- | --- | --- | --- |
| Claude Sonnet 5 | $2 / MTok | $10 / MTok | $2.50 / $4.00 | $0.20 / MTok |

The $2/$10 Sonnet 5 rate was introductory pricing scheduled to rise to
$3/$15 on 2026-09-01; Anthropic's own pricing page states that increase
**was cancelled** and $2/$10 is the standing price -- a concrete example
of why this section needs re-verification before being trusted past this
take-home, not just a disclaimer. Only cache-*read* price is modeled in
`models.yaml`'s `cached_input_per_million`; cache-*write* pricing (shown
above for reference) has no field in the current config.

## Important Quirks

- **All three providers, not just Anthropic, represent content-policy
  rejection as a finish/stop reason on a successful response, not a raised
  error.** Anthropic: `stop_reason: "refusal"`. OpenAI: `incomplete_details.reason: "content_filter"`.
  Gemini: `finish_reason: SAFETY` (or several siblings). `ProviderErrorKind.CONTENT_FILTER`
  exists in the normalized taxonomy for provider-neutrality (a hypothetical
  provider that *does* hard-reject), but none of the three real adapters
  raise it in their current form -- the real signal is always
  `FinishReason.CONTENT_FILTER`. Worth stating explicitly rather than
  discovering it by surprise.
- Tool definitions need zero restructuring for this provider -- our
  `ToolDefinition` already matches Anthropic's wire shape field-for-field.
- The Messages API's tokenizer changed with Claude 4.7+ ("~30% more
  tokens for the same text" per the pricing docs) -- irrelevant to this
  adapter's correctness, but relevant if anyone hand-estimates token counts
  against `context_window`.

## Official References

- https://platform.claude.com/docs/en/build-with-claude/streaming
- https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview
- https://platform.claude.com/docs/en/api/errors
- https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons
- https://platform.claude.com/docs/en/about-claude/pricing
- Installed SDK source: `anthropic/_exceptions.py` (`anthropic==1.7.0`)

---

# OpenAI

## SDK / API Used

Official `openai` Python SDK, pinned `openai==3.17.0`. **Responses API**
(`client.responses.create`), not Chat Completions. Verified via
`developers.openai.com/api/docs/guides/migrate-to-responses`: "Responses
is recommended for all new projects"; Chat Completions is the legacy shape
kept for backward compatibility. This is a real, current-as-of-check
choice, not the older/easier-to-find example API -- Chat Completions
examples are far more common in older tutorials and would have been the
"obsolete API chosen because an old example is easier" the checkpoint
explicitly warns against.

## Configured Model

`gpt-5.6-terra` (internal id and provider model id are the same string
here). 1,050,000 token context window, 128,000 max output tokens. GPT-5.6
ships in three tiers (Sol $4/$20, **Terra $2/$12**, Luna $0.20/$1.20);
Terra was chosen for rough price-tier parity with Claude Sonnet 5, not
because it's the only option -- a pricier flagship (`gpt-6-astra`,
$10/$50) exists and isn't configured.

## System Prompt Handling

Top-level `instructions` string parameter -- structurally identical in
*position* to Anthropic's `system`, different field name.

## Message Format

The Responses API represents a conversation as an `input` array of typed
**items**, not a flat list of role/content messages the way both Anthropic
and (historically) OpenAI's own Chat Completions API do. This is the
biggest structural difference among the three providers. Item types this
adapter produces/consumes:

- `{"type": "message", "role": "user"|"assistant"|"system"|"developer", "content": [...]}`
  for actual conversation turns; `content` parts are
  `{"type": "input_text", "text": ...}` or
  `{"type": "input_image", "image_url": "data:<mime>;base64,<data>"}`.
- `{"type": "function_call", "call_id", "name", "arguments": "<JSON string>"}`
  for a prior tool call -- **a separate top-level item**, not nested inside
  a message's content list. A single normalized `Message` with mixed
  `TextBlock` + `ToolUseBlock` content therefore splits into *two* input
  items on this provider (`OpenAIAdapter._translate_input`), unlike
  Anthropic/Gemini where one message stays one item.
- `{"type": "function_call_output", "call_id", "output": "<text>"}` for a
  tool result. **No `is_error` field** -- unlike Anthropic's
  `tool_result.is_error`, OpenAI's shape has nothing to flag a tool
  failure distinctly from a normal result. Currently passed through as
  plain `output` text regardless of `ToolResultBlock.is_error`; flagged
  here rather than silently working around it with a text prefix that
  wasn't asked for.

## Streaming

Verified directly against the installed SDK's own type definitions
(`openai/types/responses/response_stream_event.py` -- **over 60** distinct
event types exist; this adapter normalizes 7 of them and ignores the rest,
per the assignment's own "handle unknown event types gracefully"
guidance). Field names confirmed from each event's own source file, not
guessed:

- `response.output_text.delta` -- `.delta` (str)
- `response.function_call_arguments.delta` -- `.delta` (str), `.item_id`
- `response.function_call_arguments.done` -- `.arguments` (full JSON str), `.item_id`
- `response.output_item.added` -- `.item` (a `ResponseFunctionToolCall` for tool calls, carrying both `.id` and `.call_id` -- see Tool Calls)
- `response.completed` / `.failed` / `.incomplete` -- all three carry `.response`, the full `Response` object, normalized identically

## Tool Definitions

`{"type": "function", "name", "description", "parameters": <json schema>, "strict": null}`.
`parameters` takes our `input_schema` directly.

## Tool Calls

A `function_call` output item: `{"type": "function_call", "call_id", "name", "arguments": "<JSON string>"}`.
Unlike Anthropic's streamed `tool_use.input` (an object filled in place),
OpenAI's non-streaming `arguments` is always a complete JSON **string** to
be parsed once, matching the streaming `.done` event's shape.

## Tool Argument Streaming

Fragments arrive via `response.function_call_arguments.delta.delta` (raw
JSON string pieces, same "don't parse until done" rule as Anthropic) and
are finalized by `response.function_call_arguments.done.arguments` (the
complete string). **A genuine correlation trap found while building this
adapter, not assumed:** the item's internal `id` (what the delta/done
events key off via `item_id`) and its `call_id` (what a later
`function_call_output` must reference) are **two different strings** for
the same tool call. `item.id` is only given once, on
`response.output_item.added`; this adapter tracks `item_id -> call_id` at
that point so every later delta/done event can carry the *correct*
normalized id. Tested explicitly in
`test_stream_correlates_function_call_arguments_by_item_id_not_call_id`.

## Usage Reporting

`response.usage`: `input_tokens`, `output_tokens`,
`input_tokens_details.cached_tokens`, `output_tokens_details.reasoning_tokens`
-- confirmed directly from `openai/types/responses/response_usage.py`.
This is the only one of the three providers whose SDK types report a
reasoning-token count in the shape our `Usage.reasoning_tokens` expects
(Anthropic bills thinking tokens into `output_tokens`; Gemini's
`usage_metadata` has no equivalent field at all).

## Finish Reasons

The Responses API has no per-turn `stop_reason` string the way Anthropic
does -- instead a response-level `status`
(`completed`/`failed`/`in_progress`/`cancelled`/`queued`/`incomplete`) plus,
when `status == "incomplete"`, an `incomplete_details.reason`
(confirmed from `openai/types/responses/response.py`):

| OpenAI `status` / `incomplete_details.reason` | Normalized `FinishReason` |
| --- | --- |
| `status == "failed"` | `ERROR` |
| `incomplete_details.reason == "max_output_tokens"` or `"max_messages"` | `LENGTH` |
| `incomplete_details.reason == "content_filter"` | `CONTENT_FILTER` |
| any output item has `type == "function_call"` | `TOOL_USE` (checked before falling back to `status`) |
| `status == "completed"` | `STOP` |
| anything else (`cancelled`, `queued`, `incomplete_details.reason == "steered"`) | `UNKNOWN` |

## Errors

Verified directly against the installed SDK's own `openai._exceptions`
module:

| Exception class | HTTP status | Normalized kind |
| --- | --- | --- |
| `AuthenticationError`, `PermissionDeniedError`, `OAuthError` | 401, 403, 401 | `AUTH` |
| `RateLimitError` | 429 | `RATE_LIMIT` |
| `APITimeoutError` | -- | `TIMEOUT` |
| `BadRequestError`, `NotFoundError`, `ConflictError`, `UnprocessableEntityError` | 400, 404, 409, 422 | `BAD_REQUEST` |
| `APIStatusError`, `APIConnectionError`, `InternalServerError` | 5xx / connection-level | `SERVER_ERROR` |
| anything else | -- | `UNKNOWN` |

**Real SDK classes that exist but are deliberately not in this mapping:**
`ContentFilterFinishReasonError` and `LengthFinishReasonError` are genuine
exception classes in `openai._exceptions`, but they're raised only by the
SDK's `.parse()` structured-output convenience helper, which this adapter
doesn't call -- this adapter's actual code path (`.create()`, `stream=True`)
surfaces both conditions via `incomplete_details.reason` on a normal
response instead (see Finish Reasons). Listing this because it would have
been an easy, plausible-looking mapping mistake to make from the class
names alone without checking which code path actually raises them.

## Cancellation

Not live-tested. Same structural guarantee as the Anthropic adapter:
`except Exception` does not intercept `asyncio.CancelledError`.

## Pricing

Verified 2026-09-22 against `developers.openai.com/api/docs/pricing`
(current canonical location -- `platform.openai.com/docs/pricing` now
redirects here) and cross-checked via independent search aggregation.

| | Input | Cached input | Output |
| --- | --- | --- | --- |
| GPT-5.6 Terra | $2 / MTok | $0.20 / MTok | $12 / MTok |

## Notable Quirks

- The Responses API's item-based structure is the single biggest
  reconciliation cost in this adapter relative to Anthropic/Gemini's more
  message-shaped APIs -- one normalized `Message` can expand into multiple
  `input` items.
- 60+ granular SSE event types exist for capabilities entirely outside
  this project's scope (audio, MCP, code interpreter, image generation,
  shell, web search) -- all silently skipped by `stream()`'s `elif` chain,
  per the assignment's "handle unknown event types gracefully" guidance,
  not because they were overlooked.

## Official References

- https://developers.openai.com/api/docs/guides/migrate-to-responses
- https://developers.openai.com/api/docs/pricing
- Installed SDK source: `openai/types/responses/*.py`, `openai/_exceptions.py` (`openai==3.17.0`)

---

# Gemini

## SDK / API Used

Official `google-genai` Python SDK, pinned `google-genai==2.24.0`. **Not**
`google-generativeai` -- verified via `ai.google.dev/gemini-api/docs/migrate`:
the older package is explicitly "now deprecated" (its own GitHub repo is
renamed `deprecated-generative-ai-python`), superseded by `google-genai`
since Gemini 2.0, GA since May 2025. Uses the async namespace,
`client.aio.models.generate_content` / `.generate_content_stream`.

## Configured Model

`gemini-2.5-flash` (internal id and provider model id are the same
string). 1,000,000 token context window. **Max output tokens: not
confirmed from an official source** -- left `null` in `models.yaml` rather
than guessed; still open for CP-04+ if it becomes load-bearing. Chosen
over the newer 3.x Flash/Pro tiers (also confirmed available: 3.6 Flash
$1.50/$7.50, 3.1 Pro Preview $2/$12, 3.5 Flash-Lite $0.30/$2.50) because
it's stable/GA rather than `-preview`, which matters more for a config a
reviewer might actually run against than matching the other two
providers' newest tier.

## System Prompt Handling

`GenerateContentConfig.system_instruction` -- a config field, not a
request-level parameter the way Anthropic's `system` or OpenAI's
`instructions` are top-level.

## Message Format

`Content(role: "user"|"model", parts: [Part])`. **Only two roles exist --
confirmed directly from the installed SDK's docstring: "Must be either
'user' or 'model'."** No `assistant` (Gemini calls it `model`), no third
role for tool results the way Anthropic/OpenAI have none either -- but
where Anthropic's fix is "route Role.TOOL through user, unchanged shape,"
Gemini's `FunctionResponse` additionally requires the function's *name*,
which our `ToolResultBlock` doesn't carry (only `tool_use_id`). This
adapter resolves it by looking back through the *same request's* own
message history for the `ToolUseBlock` that originated the call and
recovering `name` from there (`GeminiAdapter._translate_contents`) --
not a CP-02 contract change, since the information is already present
earlier in every real conversation.

`Part` factory methods used (confirmed from the installed SDK):
`Part.from_text(text=...)`, `Part.from_bytes(data=<raw bytes>, mime_type=...)`
(our `ImageBlock.data` is base64 *text*; the adapter decodes it to raw
bytes before calling this, since the SDK wants bytes, not a base64
string), `Part.from_function_call(name=, args=)`,
`Part.from_function_response(name=, response={"output": ...} | {"error": ...})`.

## Streaming

`client.aio.models.generate_content_stream(...)` yields `GenerateContentResponse`
chunks. Each chunk carries `candidates[0].content.parts` (new content since
the last chunk) and, once populated (typically the final chunk),
`candidates[0].finish_reason` and `usage_metadata`. No distinct "delta"
event types the way Anthropic/OpenAI have -- every chunk is a
(partial) response object, not a small typed event.

## Tool Definitions

`FunctionDeclaration(name=, description=, parameters_json_schema=<raw JSON schema>)`.
Confirmed from the installed SDK: `parameters_json_schema` is a real,
documented field, "mutually exclusive with `parameters`" (the SDK's own
typed `Schema` object) -- this adapter uses it specifically so translation
stays a near-passthrough of `ToolDefinition.input_schema`, the same way it
does for Anthropic, rather than converting into Gemini's separate typed
schema representation.

## Tool Calls

A `function_call` part: `FunctionCall(id: Optional[str], name, args: dict)`.
**`args` arrives already parsed** -- see Tool Argument Streaming.

## Tool Argument Streaming

**Gemini does not stream fragmented tool-call arguments at all**, unlike
Anthropic and OpenAI. Confirmed directly from the installed SDK's own type
definitions: `FunctionCall.partial_args` and the `PartialArg` type both
carry the exact docstring "This field is not supported in Gemini API."
The whole parsed `args` dict arrives in a single chunk. This adapter
reflects that honestly rather than papering over the difference: it emits
`ToolUseStartEvent` immediately followed by `ToolUseCompleteEvent`, with
**no `ToolUseDeltaEvent` in between** -- synthesizing a fake delta to look
uniform with the other two adapters would misrepresent what this provider
actually does. Tested explicitly in
`test_stream_emits_start_then_complete_with_no_delta_for_tool_calls`.

## Usage Reporting

`usage_metadata`: `prompt_token_count`, `candidates_token_count`,
`cached_content_token_count`. **No reasoning-token field at all** in the
installed SDK's `GenerateContentResponseUsageMetadata` -- `Usage.reasoning_tokens`
is always `None` for this adapter, same "not reported" semantics as
Anthropic (for a different underlying reason: Anthropic bills thinking
tokens elsewhere; Gemini's API just doesn't expose the metric here).

## Finish Reasons

Verified directly from the installed SDK's `FinishReason` enum
(`google/genai/types.py`):

| Gemini `finish_reason` | Normalized `FinishReason` |
| --- | --- |
| `STOP` | `STOP` |
| `MAX_TOKENS` | `LENGTH` |
| `SAFETY`, `RECITATION`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `SPII`, `IMAGE_SAFETY` | `CONTENT_FILTER` |
| `LANGUAGE`, `OTHER`, `MALFORMED_FUNCTION_CALL`, `UNEXPECTED_TOOL_CALL`, `FINISH_REASON_UNSPECIFIED` | `UNKNOWN` |

**No distinct "the model called a tool" value exists in this enum at
all** -- a function-call turn still reports `STOP`. This adapter detects
`TOOL_USE` by inspecting the response/chunk content for a `function_call`
part *before* consulting the raw `finish_reason`
(`_normalize_finish_reason` / `_normalize_finish_reason_from_raw`), the
same content-first pattern used for OpenAI, for the same underlying
reason: neither provider's own "why did generation stop" field
distinguishes a tool call from a normal stop.

## Errors

Verified directly from the installed SDK's own `google.genai.errors`
module -- **structurally the flattest of the three providers**: just
`APIError` (base, carries `.code: int` and `.status: str`), `ClientError`
(4xx) and `ServerError` (5xx). No per-status-code exception subclasses the
way Anthropic/OpenAI both provide. `GeminiAdapter._translate_error`
therefore branches on the numeric `.code` itself rather than on exception
*type*:

| `APIError.code` | Normalized kind |
| --- | --- |
| 401, 403 | `AUTH` |
| 429 | `RATE_LIMIT` |
| 400, 404, 409 | `BAD_REQUEST` |
| 413 | `CONTEXT_LENGTH` |
| 408, 504 | `TIMEOUT` |
| any other 5xx (falls back to `isinstance(exc, ServerError)`) | `SERVER_ERROR` |
| anything not an `APIError` at all | `UNKNOWN` |

This is the clearest concrete illustration in the whole project of why the
adapter boundary matters: the *shape of the translation code itself*
differs per provider (a code-lookup dict here vs. an isinstance chain over
distinct classes for Anthropic/OpenAI), and none of that shows up above
`ProviderError(kind=...)`.

## Cancellation

Not live-tested. Same structural guarantee as the other two adapters.

## Pricing

Verified 2026-09-22 against `ai.google.dev/gemini-api/docs/pricing`
(official Google AI docs, fetched directly as both the rendered page and
its `.md.txt` export for cross-checking) and independent search
aggregation.

| | Input | Output |
| --- | --- | --- |
| Gemini 2.5 Flash | $0.30 / MTok | $2.50 / MTok |

Gemini's context-caching price for this model was **not** confidently
confirmed from an official source during this check (fetched sources
disagreed on naming/pricing for embedding- and caching-related SKUs) --
`models.yaml`'s `cached_input_per_million` is `null` for this model, not a
guessed number.

## Notable Quirks

- Flattest error hierarchy of the three (see Errors) -- the most
  significant per-provider difference in how much translation *logic*
  (versus translation *data*) an adapter needs.
- No fragmented tool-argument streaming at all (see Tool Argument
  Streaming) -- the opposite end of the spectrum from Anthropic's
  explicitly-documented "arguments stream a fragment at a time."
- `FunctionResponse` needing the function *name*, not just a call id, was
  the one place this checkpoint came closest to needing a CP-02 contract
  change -- resolved by request-scoped lookup instead (see Message Format).

## Official References

- https://ai.google.dev/gemini-api/docs/migrate
- https://ai.google.dev/gemini-api/docs/pricing
- Installed SDK source: `google/genai/types.py`, `google/genai/errors.py`, `google/genai/models.py` (`google-genai==2.24.0`)
