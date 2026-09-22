# Provider Notes

This document records concrete, sourced facts about each provider's API --
pricing, model ids, and (once each adapter is actually built in CP-03) the
request/response/streaming/error shape differences an adapter has to
translate. Every price and model id below was checked against an official
first-party source; anything not yet confirmed is marked **TBD (CP-03)**
rather than guessed. Do not trust pricing here beyond the date it was
verified -- see the note on Anthropic below about a scheduled Sonnet 5
price change that was announced and then cancelled.

## Anthropic

**Pricing verified 2026-09-22** against
[platform.claude.com/docs/en/about-claude/pricing](https://platform.claude.com/docs/en/about-claude/pricing)
(official Anthropic docs), cross-checked against
[claude.com/pricing](https://claude.com/pricing) via search aggregation.

| Model (config id `claude-sonnet`) | API model id | Input | Output | Cache write (5m / 1h) | Cache read (hit) | Context | Max output |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Claude Sonnet 5 | `claude-sonnet-5` | $2 / MTok | $10 / MTok | $2.50 / $4.00 | $0.20 / MTok | 1,000,000 | 128,000 |

The Sonnet 5 launch price ($2/$10) was introductory pricing scheduled to
rise to $3/$15 on 2026-09-01; Anthropic's own pricing page states that
increase **was cancelled** and $2/$10 is now the standard, ongoing price.
Noted here as a concrete example of why pricing needs re-verification
before being trusted beyond this take-home, not just in principle.

Only cache *read* (hit) price is modeled in `models.yaml`'s
`cached_input_per_million` -- Anthropic also prices cache *writes*
separately (shown above for reference) and this config has no field for
that yet, since nothing in CP-02 accounts for it.

- **Request/message format:** TBD (CP-03) -- Anthropic's Messages API uses
  a `system` top-level string/array (not a system-role message) and a
  `content` array of typed blocks per message; exact block-name mapping to
  `TextBlock`/`ImageBlock`/`ToolUseBlock`/`ToolResultBlock` to be confirmed
  against the SDK when the adapter is written.
- **System prompt:** TBD (CP-03) -- confirmed conceptually top-level
  `system` param, not a message; exact multi-block system prompt behavior
  not yet verified.
- **Streaming:** TBD (CP-03) -- Anthropic streams typed SSE events
  (`content_block_start`/`delta`/`stop`, `message_delta`, etc.); mapping
  each to our `StreamEvent` variants is adapter work.
- **Usage:** TBD (CP-03) -- known from the pricing page that responses
  report `input_tokens`/`output_tokens` and (with tools) a
  `server_tool_use` block; whether `cache_read_input_tokens` /
  `cache_creation_input_tokens` map onto our `cached_input_tokens` needs
  confirming against a real response.
- **Finish reasons:** TBD (CP-03) -- Anthropic's `stop_reason` values
  (`end_turn`, `max_tokens`, `tool_use`, `stop_sequence`, etc.) need
  mapping to `FinishReason`.
- **Tool calling / tool streaming:** TBD (CP-03).
- **Errors:** TBD (CP-03) -- `anthropic` SDK exception hierarchy
  (`AuthenticationError`, `RateLimitError`, etc.) needs mapping to
  `ProviderErrorKind`.
- **Cancellation:** TBD (CP-03) -- expected to be ordinary
  `asyncio.CancelledError` propagation through the SDK's async HTTP
  client; needs confirming the SDK doesn't swallow it.
- **Embeddings:** Anthropic does not offer a first-party embeddings
  endpoint as of this check. `claude-sonnet`'s `capabilities.embeddings`
  is `false`; an `AnthropicAdapter` would rely on `Provider.embed()`'s
  default `UNSUPPORTED` error.
- **Notable quirks:** none recorded yet -- nothing has been built against
  the live API.

## OpenAI

**Pricing verified 2026-09-22** via search aggregation across
multiple pricing-tracking sites, cross-checked against
[developers.openai.com/api/docs/pricing](https://developers.openai.com/api/docs/pricing)
(current canonical location; `platform.openai.com/docs/pricing` now
redirects here).

| Model (config id `gpt-5.6-terra`) | API model id | Input | Cached input | Output | Context | Max output |
| --- | --- | --- | --- | --- | --- | --- |
| GPT-5.6 Terra | `gpt-5.6-terra` | $2 / MTok | $0.20 / MTok | $12 / MTok | 1,050,000 | 128,000 |

GPT-5.6 ships in three tiers (Sol $4/$20, Terra $2/$12, Luna $0.20/$1.20);
Terra was picked as the configured default for rough price-tier parity
with Claude Sonnet 5. GPT-6 Astra ($10/$50) exists as a pricier flagship
option not currently configured. This is a config choice, not something
CP-02 needs to get "right" -- swapping which OpenAI tier is configured is
a `models.yaml` edit, nothing more.

- **Request/message format:** TBD (CP-03) -- OpenAI's newer Responses API
  and the older Chat Completions API structure messages/content
  differently; which one the adapter targets is a CP-03 decision.
- **System prompt:** TBD (CP-03) -- OpenAI conventionally uses a
  system-role message rather than a separate top-level field (contrast
  with Anthropic); exact current API shape to confirm.
- **Streaming:** TBD (CP-03) -- SSE-based; event shape depends on which of
  the two APIs above the adapter targets.
- **Usage:** TBD (CP-03) -- whether/how reasoning-token accounting (for
  reasoning-capable model variants, not the configured `gpt-5.6-terra`)
  surfaces needs confirming against a real response.
- **Finish reasons:** TBD (CP-03) -- OpenAI's `finish_reason` values
  (`stop`, `length`, `tool_calls`, `content_filter`, etc.) need mapping to
  `FinishReason`.
- **Tool calling / tool streaming:** TBD (CP-03).
- **Errors:** TBD (CP-03) -- `openai` SDK exception hierrarchy needs
  mapping to `ProviderErrorKind`.
- **Cancellation:** TBD (CP-03).
- **Embeddings:** OpenAI has first-party embedding models (historically
  `text-embedding-3-small`/`-large`); not configured in `models.yaml` yet
  since CP-02 defines the contract only -- an embedding-capable OpenAI
  model entry with sourced pricing is CP-05 (RAG) work, at which point
  `OpenAIAdapter.embed()` would override the base class default instead
  of raising `UNSUPPORTED`.
- **Notable quirks:** none recorded yet -- nothing has been built against
  the live API.

## Gemini

**Pricing verified 2026-09-22** against
[ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing)
(official Google AI docs).

| Model (config id `gemini-2.5-flash`) | API model id | Input | Output | Context | Max output |
| --- | --- | --- | --- | --- | --- |
| Gemini 2.5 Flash | `gemini-2.5-flash` | $0.30 / MTok | $2.50 / MTok | 1,000,000 | **TBD (CP-03)** |

Gemini 2.5 Flash was chosen over the newer, pricier 3.x Flash/Pro tiers
(also confirmed available: 3.6 Flash $1.50/$7.50, 3.1 Pro Preview
$2/$12 up to 200K tokens then $4/$18, 3.5 Flash-Lite $0.30/$2.50) because
it's the stable, generally-available option rather than a `-preview`
model, which matters more for a config a reviewer might actually run
against than matching the other two providers' newest tier. Swapping the
configured Gemini model is, again, a one-line `models.yaml` change.

- **Request/message format:** TBD (CP-03) -- Gemini's `contents` array
  and `Part` union (text/inline_data/function_call/function_response) map
  to our content blocks differently than either Anthropic's or OpenAI's
  shape; exact mapping is adapter work.
- **System prompt:** TBD (CP-03) -- Gemini has a `system_instruction`
  field, conceptually similar to Anthropic's top-level `system`.
- **Streaming:** TBD (CP-03) -- `generateContentStream`/
  `streamGenerateContent`; event shape to confirm against the SDK.
- **Usage:** TBD (CP-03) -- `usageMetadata` field names
  (`promptTokenCount`, `candidatesTokenCount`, etc.) need mapping to
  `Usage`.
- **Finish reasons:** TBD (CP-03) -- Gemini's `finishReason` enum
  (`STOP`, `MAX_TOKENS`, `SAFETY`, etc.) needs mapping to `FinishReason`.
- **Tool calling / tool streaming:** TBD (CP-03) -- Gemini calls this
  "function calling"; streaming behavior for partial function-call
  arguments needs confirming.
- **Errors:** TBD (CP-03) -- `google-genai`/`google-generativeai` SDK
  exception hierarchy (e.g. `google.api_core.exceptions.ResourceExhausted`
  for rate limits) needs mapping to `ProviderErrorKind`.
- **Cancellation:** TBD (CP-03).
- **Embeddings:** Google does offer first-party embedding models
  (`gemini-embedding-*`); not configured in `models.yaml` yet for the same
  reason as OpenAI above -- deferred to CP-05, and pricing for it was not
  confidently confirmed during this check (some fetched sources disagreed
  on naming/pricing for embedding-specific models, so it's intentionally
  left out rather than guessed).
- **Notable quirks:** `gemini-2.5-flash`'s max output token limit was not
  found in the pricing/docs pages checked; `models.yaml` leaves
  `max_output_tokens: null` for this model rather than guessing. Needs a
  dedicated docs lookup (likely the model-listing page, not the pricing
  page) during CP-03.

## Sourcing method (for anyone re-verifying)

Pricing and model ids above came from official first-party pages
(`platform.claude.com`, `developers.openai.com`, `ai.google.dev`) fetched
directly, cross-checked against independent search-aggregated summaries
for consistency. Anthropic's exact current self-serve model ids
(`claude-sonnet-5`, `claude-opus-5`, `claude-haiku-4-5-20251001`,
`claude-fable-5-1`) were additionally corroborated against this session's
own known model identity, which matched exactly -- a useful independent
check on top of the fetched pages. Numbers that came back inconsistent
across sources, or that no first-party page confirmed directly (Gemini's
embedding pricing, `gemini-2.5-flash`'s max output tokens), were left as
**TBD** rather than reconciled by guessing.
