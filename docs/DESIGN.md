# Design Decisions

This document is written incrementally, one section per checkpoint, rather
than reconstructed at the end.

## CP-01 Foundation

### Frontend/backend separation

`frontend/` (React + TypeScript + Vite) and `backend/` (Python + FastAPI)
are independent projects with their own dependency management, talking
over plain HTTP. The frontend never imports backend code or vice versa;
`frontend/src/api/` is the only place that knows the backend's base URL.

### Persistence choice: native PostgreSQL, no Docker

CP-00 planned Postgres + pgvector via `docker compose up`. The development
machine used for this build has no Docker/WSL installed, and installing
them would have required a Windows reboot that isn't practical mid-build.
Rather than silently switch database technology, we kept **PostgreSQL**
(installed as a native Windows service) and deferred **pgvector**: vector
similarity for RAG (CP-05) will be computed in Python over embeddings
stored in a plain column, which the assignment explicitly allows ("a
hand-rolled [vector store] is fine, defend it"). Everything else --
tenant tables, row-level security, the connection/migration pattern --
is exactly as planned. If a reviewer's machine has Docker, the same
schema works unchanged against a containerized Postgres.

### Tenant identity source

A single header, `X-Tenant-Id`, carries the caller's tenant slug (e.g.
`tenant-a`). This is resolved in exactly one place --
`app/core/tenant.py::get_tenant_context` -- which looks the slug up
against the `tenants` table and returns a validated `TenantContext`, or
rejects the request with 401. No other module reads this header.

**Known limitation, by design for a take-home:** the header is unsigned,
so any caller can claim to be any tenant by changing its value. The
assignment explicitly permits this ("a simple header or session-based
tenant identifier is fine for a take-home... we do not need real
authentication"). What we *do* enforce is what happens once a tenant
identity is accepted -- see below. Production would replace this header
with a tenant claim inside a signed session token or a per-tenant API
key, verified server-side (e.g. a bearer key hashed and looked up, or a
JWT whose claims are trusted only because they're signed) -- swapping
that in only touches `app/core/tenant.py`; nothing downstream changes.

### Tenant context flow

```
Request
  -> X-Tenant-Id header
  -> get_tenant_context() FastAPI dependency   (app/core/tenant.py)
  -> TenantContext(id, slug)                    (validated, immutable)
  -> tenant_connection(pool, tenant)             (app/db/pool.py)
  -> a Postgres connection with app.tenant_id
     set for the duration of one transaction
  -> TenantScopedRepository subclass             (app/repositories/)
  -> SQL, filtered by Postgres itself
```

Services and repositories always receive a `TenantContext` object, never
a raw header string -- that is what keeps HTTP transport concerns
(where did this identity come from, can it be forged) separate from
data-access enforcement (what can this identity see).

### Tenant data-access boundary: why it's structural, not conventional

The boundary is enforced by PostgreSQL itself, not by application code
remembering to filter:

- Every tenant-owned table has `ROW LEVEL SECURITY` **enabled and
  forced** (`ALTER TABLE ... FORCE ROW LEVEL SECURITY`). Without
  `FORCE`, Postgres exempts the table's *owner* from its own policies --
  and the application connects as that owning role (`polyglot_app`,
  never a superuser), so `FORCE` is what actually makes the policy bite.
- The policy compares `tenant_id` to `current_setting('app.tenant_id',
  true)`, a Postgres session variable. The **only** way application code
  gets a connection is `app.db.pool.tenant_connection(pool, tenant)`,
  which opens a transaction and sets that variable via
  `set_config('app.tenant_id', $1, true)` (the parameterized equivalent
  of `SET LOCAL`, so the tenant id can never be an injection vector).
  There is no exported "raw pool" for a shortcut.
- If that setting is ever missing -- a future connection that bypasses
  `tenant_connection()` -- `current_setting(..., true)` returns `NULL`,
  and `tenant_id = NULL` is never true. The query returns **zero rows**,
  not every tenant's rows. The boundary **fails closed**.
- `NoteRepository` (the one tenant-owned resource in this checkpoint,
  see below) deliberately does **not** add its own `WHERE tenant_id =
  ...` on `SELECT`. That is the point being demonstrated: the repository
  method could not leak another tenant's row even with a missing or
  buggy filter, because Postgres is the one filtering.

**A new engineer joining Monday** who writes `SELECT * FROM notes` (no
filter at all) inside a route that calls `tenant_connection()` gets back
only their caller's tenant rows -- correct by default. The only way to
leak data would be to intentionally bypass `tenant_connection()` and
grab a raw pool connection, which is unusual enough to stand out in
review, and even then, per the fail-closed behavior above, that raw
connection sees *nothing* (not everything) unless something else sets
`app.tenant_id` on it.

**Detecting a leak in production** (documented now, not built in a
take-home): log every `app.tenant_id` a connection is opened with
alongside the request's authenticated tenant and alert on mismatch;
periodically run an admin-role query that checks for any row whose
`tenant_id` doesn't match a valid tenant, or any foreign key pointing
cross-tenant; monitor for RLS policy violation errors, which Postgres
raises distinctly from ordinary query errors.

### Notes table: a scaffold, not a feature

`notes` (see `app/db/migrations/002_notes.sql`) exists solely to prove
this pattern end-to-end before any real domain logic (conversations,
documents, etc.) is built on top of it in later checkpoints. It is not a
product feature and won't appear in the UI.

### Errors

A single FastAPI exception handler catches any unhandled exception,
logs the real error server-side, and returns a generic
`{"detail": "Internal server error"}` with no stack trace, query text,
or connection string. Provider-specific error normalization is CP-02/03
work and does not exist yet.

### What we'd do differently with more time (running list)

- Use a proper migration tool once the schema grows past a handful of
  files (plain numbered `.sql` is fine for CP-01's one table).
- Real authentication in place of the `X-Tenant-Id` header, as above.

## CP-02 Provider Abstraction

### The flow

```
Application (chat service, RAG service, API routes -- none exist yet)
  -> internal model id, e.g. "claude-sonnet"           (a string the caller picks)
  -> ModelRegistry.get(model_id)                        (app/providers/models.py)
  -> ModelConfig(provider="anthropic", provider_model_id="claude-sonnet-5",
                 context_window, capabilities, pricing)
  -> ProviderRegistry.get(model_config.provider)         (app/providers/registry.py)
  -> Provider (an ABC -- app/providers/base.py)
  -> Adapter (CP-03; does not exist yet)                 e.g. AnthropicAdapter
  -> provider.complete(request) / provider.stream(request)
  -> CompletionResponse / AsyncIterator[StreamEvent]      (app/providers/contracts.py)
```

Application code only ever touches the last three normalized types
(`CompletionRequest`, `CompletionResponse`/`StreamEvent`) and the two
registries. It never imports `anthropic`, `google.genai`, or `openai`, and
never branches on a provider name -- `if provider == "anthropic"` should
not exist anywhere above the adapter layer, and a repository-wide grep
confirms it doesn't (the only files mentioning provider names are
`models.yaml` itself, the reserved `*_api_key` settings from CP-01, and
docstrings explaining *why* the abstraction is shaped this way).

### Why application code only depends on normalized types

Two ids are kept deliberately separate everywhere in this layer:

- **Internal model id** (`"claude-sonnet"`) -- what `CompletionRequest.model`
  carries, what a future chat service and its API route deal in exclusively.
- **Provider model id** (`"claude-sonnet-5"`) -- the literal string an
  adapter sends upstream, resolved from `models.yaml` via `ModelConfig`.

If application code carried provider model ids directly, renaming a model
upstream (or repointing our "claude-sonnet" entry at a different Anthropic
model entirely) would mean hunting down every place that string appears.
Because only `models.yaml` and the adapter that reads `provider_model_id`
off `ModelConfig` ever see it, that change is one line in one YAML file.

The same reasoning extends to every other normalized type. `Message` /
`ContentBlock` (`app/providers/messages.py`) represent what was said, not
how Anthropic's content blocks, Gemini's `Part`, or OpenAI's Responses API
content objects represent it -- that translation is entirely an adapter's
job, in both directions (normalized request -> provider call, provider
response/native exception -> normalized response/`ProviderError`).
`StreamEvent` (seven flat, tagged event kinds in `contracts.py`, not one
event with a dozen optional fields) is what a future SSE endpoint will
serialize to the browser, regardless of which provider's streaming
protocol produced the underlying tokens.

### Two notable design decisions

**Usage fields are `int | None`, not `int = 0`.** A provider that doesn't
report `reasoning_tokens` at all and a provider that reports it *as* zero
are different facts, and collapsing both to `0` would make them
indistinguishable the moment a usage/cost dashboard (CP-06, if time
allows) tries to build real numbers from this data. `None` means "not
reported"; `0` means "reported, and it was zero." `PricingConfig` mirrors
this for `cached_input_per_million`, which is `None` for Gemini right now
specifically because its cached-input price isn't yet confirmed from an
official source -- not because it's known to be free.

**No cancellation field on `CompletionRequest`.** The assignment's
TypeScript sketch implies an `AbortSignal`-shaped mechanism; Python
already has one. Cancelling the `asyncio.Task` running a request is the
idiomatic way to stop in-flight async work, and FastAPI already cancels
the handling task when a client disconnects mid-stream. A well-behaved
adapter's `await http_client.post(...)` unwinds on
`asyncio.CancelledError` like any other awaited call, which is expected to
propagate the cancellation to the underlying HTTP request without any
extra plumbing. Forcing an `AbortSignal`-style field onto the request
would duplicate a mechanism Python already has for free, and would be one
more thing every adapter has to remember to check. This gets exercised for
real in CP-04, once there's an actual SSE endpoint and a real adapter to
cancel.

### Embedding support: why `embed()` isn't abstract

`Provider.complete()` and `Provider.stream()` are `@abstractmethod` --
every provider must support chat. `embed()` is not: its base-class
implementation raises `ProviderError(kind=UNSUPPORTED)`, and only
providers that actually do embeddings override it. The alternative
considered was a separate `EmbeddingProvider` protocol that only
embedding-capable adapters implement; that's more statically type-safe,
but it forces `ProviderRegistry` to hand back a union type and callers to
`isinstance`-check before every embedding call -- exactly the kind of
provider-shape branching this checkpoint exists to eliminate. The chosen
design keeps one interface and one registry. A caller that wants to check
ahead of time can (`model_config.capabilities.embeddings`); a caller that
doesn't gets a normalized error instead of an `AttributeError` or a
silent no-op.

### `ProviderErrorKind.UNSUPPORTED`

The assignment's required error categories are `auth`, `rate_limit`,
`context_length`, `content_filter`, `timeout`, `server_error`,
`bad_request`, plus `unknown` for safely handling unexpected upstream
failures. `UNSUPPORTED` is an addition beyond that list, added because
`embed()` needed a way to say "this provider/model doesn't do this at
all" that's distinguishable from `BAD_REQUEST` (the request itself was
malformed) -- calling `.embed()` on a chat-only provider isn't a malformed
request, it's a capability mismatch.

### Adding a provider (the target process, not yet proven end-to-end)

CP-02's fake-provider test (`tests/providers/test_extensibility.py`)
proves the *registry and interface* are provider-agnostic: a throwaway
`FakeProvider` satisfies `Provider`, gets registered, and generic
resolution code (`ModelRegistry.get` -> `ProviderRegistry.get` ->
`provider.complete()`/`.stream()`) drives it with zero special-casing.
CP-03 is what proves this works for a *real* SDK-backed provider; CP-07's
interview prep will revisit whether it held up. Until then, the intended
process for adding a provider is:

1. Write one adapter class (`app/providers/<name>.py`, does not exist
   yet) implementing `Provider` -- translate `CompletionRequest` to the
   SDK's call shape, translate native responses/streaming events back to
   `CompletionResponse`/`StreamEvent`, catch native exceptions and
   re-raise `ProviderError`.
2. Add an entry to `models.yaml` with real, sourced pricing (see
   `docs/PROVIDER_NOTES.md`) and a `provider:` value matching the
   adapter's `id`.
3. Add the credential env var to `Settings` (`app/core/config.py`) and
   `.env.example` if the adapter needs one.
4. Register the adapter instance with `ProviderRegistry` wherever
   providers get wired up at startup (does not exist yet -- CP-03/04).
5. Write adapter-specific tests (request translation, response
   translation, native-error-to-`ProviderError` mapping) alongside the
   existing contract tests.

No step touches a chat service, a RAG service, an API route, the
frontend, or any of `messages.py`/`contracts.py`/`errors.py`/`registry.py`
-- if adding a real provider later turns out to require touching those,
that's a sign the abstraction has a gap CP-03 needs to fix, not something
to route around.

## CP-03 Provider Adapter Boundary

CP-03 built the three adapters the process above describes and proved it
holds for real SDKs, not just the CP-02 fake provider.

```
Application (none exists yet -- CP-04)
  -> CompletionRequest                            (app/providers/contracts.py)
  -> ModelRegistry.get(internal model id)          (app/providers/models.py)
  -> ProviderRegistry.get(model_config.provider)   (app/providers/registry.py,
                                                      wired by app/providers/wiring.py)
  -> Provider interface                            (app/providers/base.py)
        |              |              |
   Anthropic         Gemini         OpenAI
    Adapter           Adapter        Adapter
        |              |              |
   anthropic SDK    google-genai   openai SDK
   (Messages API)   (aio.models)   (Responses API)
```

and, for the response side:

```
Provider-native response/stream event
  -> Adapter's _normalize_*() / _translate_error()
  -> CompletionResponse / StreamEvent / ProviderError   (normalized)
  -> Application
```

**Why provider-native types never cross the adapter boundary:** every
adapter method that touches an SDK type (`anthropic.types.Message`,
`openai.types.responses.Response`, `google.genai.types.GenerateContentResponse`,
their respective streaming event unions, their respective exception
hierarchies) is a private, adapter-local helper (`_build_request`,
`_normalize_content`, `_translate_error`, ...). `complete()` and
`stream()` -- the only public surface `Provider` defines -- are typed to
return/yield only `CompletionResponse` and `StreamEvent`, both Pydantic
models with no field capable of holding an arbitrary SDK object. There is
structurally nowhere for a native type to leak into even by accident; the
type checker (were one configured) or simply Pydantic validation at
construction time would reject it.

**Every provider's SDK shape earns its own adapter rather than a shared
translation layer.** The three adapters were built independently, not by
copying one and renaming variables (verified by how differently each
`_translate_error` is *shaped*, not just parameterized -- see below), and
the differences turned out to be real and adapter-boundary-appropriate,
not application-level:

- **Roles.** Anthropic and Gemini both collapse our `Role.TOOL` into
  a `"user"`-equivalent message, but need different extra information to
  do it (Gemini's `FunctionResponse` needs the function's *name*, recovered
  by scanning the same request's own message history -- see
  `docs/PROVIDER_NOTES.md`, Gemini "Message Format"). OpenAI's Responses
  API needs no such collapsing at all -- tool results are their own item
  type, `function_call_output`, referenced by id alone.
- **Streaming granularity.** Anthropic and OpenAI both fragment tool-call
  arguments across multiple delta events (and both require *not* parsing a
  fragment as standalone JSON); Gemini's own SDK explicitly documents that
  it does not do this at all, handing back one fully-parsed dict per call.
  The Gemini adapter emits `ToolUseStartEvent` immediately followed by
  `ToolUseCompleteEvent` with **no delta event between them**, reflecting
  what the provider actually does rather than synthesizing a fake fragment
  for uniformity.
- **Error translation shape, not just error translation data.** Anthropic
  and OpenAI both expose a typed exception subclass per HTTP status
  (`AuthenticationError`, `RateLimitError`, ...), so each adapter's
  `_translate_error` is an `isinstance` chain over distinct classes.
  Gemini's SDK gives every HTTP error the same two classes
  (`ClientError`/`ServerError`), both carrying a numeric `.code` -- so that
  adapter's `_translate_error` is a status-code lookup instead. The
  *normalized output* (`ProviderError(kind=...)`) is identical in shape
  across all three; only the adapter-local code that produces it differs,
  exactly where it's supposed to.
- **Content filtering is finish-reason-level for all three providers, not
  error-level**, discovered while building all three adapters rather than
  assumed going in -- see `docs/PROVIDER_NOTES.md`'s cross-provider note
  under Anthropic's "Important Quirks".

**Credential-driven wiring, not application-level branching.**
`app/providers/wiring.py::build_provider_registry` is the one place that
knows all three concrete adapter classes by name -- a composition root,
not application logic. It constructs and registers an adapter only when
its API key is configured (`Settings.anthropic_api_key` etc., all
`str | None`), so a missing key means that one provider is absent from the
registry, not an application startup crash; selecting an absent provider
later fails with the same `ProviderNotFoundError` CP-02 already defined
for "unknown provider id". Not wired into `app/main.py` yet -- there's no
chat service to hand the registry to until CP-04 -- so this is proven
directly against the function (`tests/providers/test_wiring.py`) rather
than only observable by booting the app.
