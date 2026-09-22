# Interview Notes

Prepared for presenting this project, not required by the assignment.
Concise, spoken-register notes -- not documentation.

## 60-Second Architecture Explanation

"Polyglot is a chat workbench that talks to Anthropic, Gemini, and OpenAI
through one normalized internal contract, so the application code never
knows or cares which provider is answering. There's a `Provider`
interface with `complete()`/`stream()`, and each provider gets one
adapter that translates its SDK's shapes into normalized types --
`CompletionRequest`, `StreamEvent`, `ProviderError`. A `ModelRegistry`
and `ProviderRegistry` resolve an internal model id like `claude-sonnet`
down to the right adapter. On top of that there's real SSE streaming
with cancellation, a RAG pipeline with server-authoritative citations so
the model can't fabricate source metadata, and an observability layer
that tracks cost and latency per turn with retry and fallback for
transient provider failures. The one architectural rule I'd defend
hardest: retry and fallback only happen before any text has streamed to
the browser -- once the user's seen real output, a failure just surfaces
as an error, it never silently restarts. And all of it sits behind
Postgres row-level security, so tenant isolation is enforced by the
database, not by an application-level filter someone could forget."

## Provider Abstraction

- Normalized domain: `CompletionRequest` in, `CompletionResponse` or a
  `StreamEvent` union out, `ProviderError` for failures -- all Pydantic
  models in `app/providers/contracts.py`/`errors.py`.
- Adapters (`anthropic_adapter.py`, `gemini_adapter.py`,
  `openai_adapter.py`) are the only code that imports a provider SDK.
  Every SDK type is translated at the adapter boundary; nothing native
  ever crosses it, structurally (the public methods are typed to return
  only normalized Pydantic models).
- Registry: `ModelRegistry` (internal id -> `ModelConfig`, from
  `models.yaml`) and `ProviderRegistry` (provider id -> live adapter
  instance, wired in `wiring.py` from whichever API keys are configured).
- Fourth provider: one new adapter file, a `Settings` field + `.env`
  var, three lines in `wiring.py`, one `models.yaml` entry, adapter
  tests. `ChatService`/RAG/retry/fallback/usage code is untouched.

## Streaming

- SSE, not WebSocket -- chat is one-directional per turn, and SSE rides
  plain HTTP.
- `fetch` + a hand-rolled parser (`sse.ts`), not `EventSource`, because
  `EventSource` can't POST and its auto-reconnect fights deliberate
  cancellation.
- Real streaming, verified in the code, not assumed: the route does
  `async for event in chat_service.stream_reply(...): yield
  _format_sse(event)` -- no "collect the full reply, then chop it into
  fake chunks" step anywhere in the path.

## Cancellation

- Stop -> `AbortController.abort()` -> fetch connection closes -> ASGI
  server sees the disconnect -> Starlette cancels the streaming task ->
  `asyncio.CancelledError` propagates through `ChatService.stream_reply`
  and the adapter's own in-flight HTTP call, closing the real upstream
  connection too.
- The subtlety worth mentioning unprompted: a plain `Task.cancel()` in a
  unit test delivers cancellation once; Starlette's real cancel-scope
  keeps re-raising it at every subsequent `await`, including a naive
  cleanup write. Fixed with `asyncio.shield()` around the persistence
  call. Found live, not by a unit test -- the unit test was passing the
  whole time.

## Multi-Tenancy

- One header, `X-Tenant-Id`, resolved once
  (`app/core/tenant.py::get_tenant_context`). Unsigned, forgeable by
  design -- allowed explicitly for a take-home.
- Enforcement is structural: every tenant-owned table has **forced** row-
  level security, keyed off a Postgres session variable
  (`app.tenant_id`) set only by one connection helper
  (`tenant_connection()`). No exported "raw pool."
- Fails closed: if the session variable is ever missing, the policy
  compares `tenant_id = NULL`, which is never true -- zero rows, not
  every tenant's rows.
- Proven adversarially: a cross-tenant test seeds another tenant's chunk
  with a vector *identical* to the querying tenant's search -- the
  highest-similarity, most-likely-to-leak case -- and it still returns
  nothing.

## RAG

- Upload -> validate (extension/size) -> extract (pypdf for PDF, no
  OCR) -> chunk (character-based sliding window) -> embed
  (`Provider.embed()`, same interface chat uses) -> store.
- Retrieval: embed the query, pull tenant/collection-scoped candidates
  via SQL (RLS + an explicit `WHERE collection_id`), rank by cosine
  similarity in Python, filter by threshold, slice to top-k.
- No pgvector -- genuinely absent from this Postgres install, checked
  directly. Tenant safety doesn't depend on where the similarity math
  runs; it depends on the SQL already being scoped before Python sees a
  row.

## Citations

- Server-authoritative, not model-trusted: `assign_source_ids` maps real
  retrieved rows to `S1`/`S2`/... **before** the model generates
  anything. The model only ever emits a bracketed id; every displayed
  field (filename, page, chunk text) comes from the server's own map.
- `extract_valid_citations` is a belt-and-suspenders filter on top of
  that -- drops any id the model invents that isn't real (`[S99]` when
  only `S1`-`S3` exist), never displays it as verified.
- No-evidence path is deterministic, not a prompt hint: zero chunks above
  threshold means the provider is never called at all.

## Observability

- One `usage_records` row per turn: provider, requested vs. final model,
  TTFT (time to first real text delta), total latency, token counts
  (`None` vs `0` distinguished throughout), cost, retry count, fallback
  flag. Never prompt/response text.
- Cost via `Decimal` math against `models.yaml` pricing, not binary
  floats. Cached-token pricing deliberately left unpriced -- Anthropic
  and OpenAI report it differently and it was never confirmed live; a
  wrong guess would silently corrupt a number someone might trust.
- `GET /api/usage/summary` aggregates per provider, tenant-scoped by the
  same RLS boundary as everything else.

## Retry/Fallback

- Retry: `rate_limit`/`server_error` only, full-jitter exponential
  backoff, bounded count. Never `auth`/`bad_request`/`context_length`/
  `content_filter` -- retrying those just fails the same way slower.
- Fallback: config-driven `fallback_model_ids` chain per model
  (`models.yaml`), same eligible kinds as retry plus `timeout`.
- The one rule that can't bend: both only happen while
  `accumulated_text == ""`. Once real output has streamed, a failure
  surfaces as an error and stops -- never a silent restart that could
  duplicate or contradict what the user already saw.
- Timeout: per-attempt `asyncio.timeout`, normalized into the same
  `ProviderError` shape, fallback-eligible but not retry-eligible
  (retrying a call that just timed out rarely helps; a different
  model/provider might).

## Security

- Provider errors never reach the browser raw -- `safe_message(kind)` is
  a fixed string per normalized error kind; the real exception (which
  CP-04 found can embed a full response body) is logged server-side
  only, never forwarded.
- Prompt injection: retrieved content is explicitly delimited and framed
  as untrusted data, tested that the framing is actually present in what
  gets sent -- stated honestly as a reduced attack surface, not a
  guarantee, since no LLM is involved in enforcing it.
- Secrets never reach the frontend; uploads are extension- and
  size-limited before extraction runs.
- Disclosed gap, not hidden: no per-tenant rate limit or spend cap --
  needs shared state (Redis or equivalent), explicitly out of scope here.

## Tradeoffs (what was intentionally cut)

- Tool execution: provider-level event normalization exists in all three
  adapters; no application-level tool registry or execution loop.
- Per-tenant rate limiting / spend caps.
- pgvector / a dedicated vector database.
- Reranking, hybrid search, semantic caching, an evaluation framework.
- OCR for scanned PDFs.
- Real authentication behind the tenant header.

## What I Would Do With More Time

1. Build the tool-calling execution loop -- the normalization work is
   already done in every adapter; the missing piece is a tool registry
   plus the re-invoke-with-tool-results loop in `ChatService`.
2. Add per-tenant rate limiting and a spend cap (Redis-backed token
   bucket), since the current cost accounting has no enforcement teeth
   behind it yet.
3. Live-verify Gemini and OpenAI chat, and OpenAI embeddings, against
   real API keys -- only Anthropic has been exercised live so far.
4. Confirm Anthropic's and OpenAI's cached-token accounting semantics
   against real responses and price them correctly instead of leaving
   them unpriced.
5. Swap pgvector in (or a dedicated vector store) once corpus size makes
   Python-side cosine similarity the retrieval bottleneck; the
   tenant/collection-scoping SQL wouldn't need to change.

## Likely Live Extension: "Add a fourth provider"

See `docs/DESIGN.md`, "Adding a Provider" for the full reasoning. The
short version, in file-path order:

1. `app/providers/<name>_adapter.py` -- implement `Provider`
   (`complete`/`stream`, `embed` only if it supports embeddings).
2. `app/core/config.py` -- one `Settings` field,
   `<name>_api_key: str | None = None`.
3. `.env.example` -- the matching env var.
4. `app/providers/wiring.py` -- three lines in
   `build_provider_registry`, same `if settings.<name>_api_key:
   registry.register(...)` shape as the other three.
5. `app/providers/models.yaml` -- one model entry, `provider: <name>`,
   real sourced pricing.
6. `tests/providers/test_<name>_adapter.py` -- fixture-based, mirroring
   the existing three.

Nothing in `ChatService`, the RAG service, the retry/fallback logic, or
the usage/cost accounting changes.

## Likely Debugging Questions

**"Streaming breaks halfway through a reply."** Check whether the
failure happened before or after the first `TextDeltaEvent` -- that's the
whole ballgame for what should happen next. Before: retry/fallback is
supposed to kick in (check `RETRYABLE_KINDS`/`FALLBACK_ELIGIBLE_KINDS` in
`retry.py`). After: it should surface an `ErrorEvent` and stop, and the
partial text should be persisted with `status="interrupted"` -- if it
silently retried instead, that's the one bug this project cannot ship
with.

**"Provider is rate-limiting us."** Normalized to
`ProviderErrorKind.RATE_LIMIT` at the adapter boundary
(`_translate_error`); eligible for both retry (full-jitter backoff, up
to `retry_max_retries`) and fallback if a next candidate model exists and
the failure happened pre-output.

**"Wrong tenant's data came back."** Shouldn't be structurally possible
-- check whether the connection in question actually came from
`tenant_connection(pool, tenant)`, or whether something bypassed it and
grabbed a raw pool connection. Per the fail-closed design, a connection
missing `app.tenant_id` sees zero rows, not the wrong tenant's rows, so
"wrong tenant's data" pointing at a *specific* tenant would be an even
stranger bug than a leak -- worth checking whether `TenantContext` itself
was constructed with the wrong id somewhere above the DB layer.

**"Citation doesn't match what's shown."** Citations are server-built
before generation (`assign_source_ids`) -- check whether the id the
model emitted was actually in the source set it was given
(`extract_valid_citations` should have dropped anything else). If a
*valid* id is showing wrong content, the bug is in how `CitedSource` was
constructed from the retrieved chunk row, not in anything model-side.

**"Vector similarity threshold seems off."** Confirm the direction first
-- higher cosine similarity is always "more similar," threshold is a
floor (`similarity >= threshold`). If results feel wrong, check whether
it's a genuine retrieval-quality issue (no evaluation dataset backs the
0.3 default) versus an actual bug in `cosine_similarity()` or the
tenant/collection `WHERE` clause feeding it candidates.

**"Context overflow / a huge conversation."** `trim_to_context_window`
drops the oldest messages first, using a ~4-chars/token estimate, until
the reserved output budget fits. The newest message is always included
even if it alone exceeds the budget -- that's intentional; it hits the
provider's real `context_length` error rather than silently returning an
empty conversation.

**"Cancellation isn't propagating."** Check for a bare `except:` or
`except Exception` anywhere in the call path that isn't `except
asyncio.CancelledError` -- `CancelledError` is a `BaseException` subclass
specifically so broad handlers don't eat it by accident. Also check
whether a cleanup write after the catch is wrapped in
`asyncio.shield()` -- without it, Starlette's cancel scope re-cancels
every subsequent `await`, including the cleanup itself.

## Submission Email Paragraph

I'm proudest of the provider abstraction -- three genuinely different
SDKs (different streaming granularity, different error-exception shapes,
different message-role handling) all collapse into one normalized
contract with zero provider-name branching in application code, verified
by grep, not just asserted. If I had more time, the first thing I'd
build is the tool-execution loop: all three adapters already normalize
provider tool-call events, so the missing piece is specifically the
application-level registry and re-invoke-with-results loop, not any new
provider work. Repo: `<REPO_URL>`.
