# AI Usage Log

This project was built with Claude Code as a pair-programming assistant.
This log is written as we go, not reconstructed afterward. Entries record
what Claude did, and specifically where its output or actions were
rejected or corrected.

## CP-00 -- Requirements analysis

**Decision:** Claude's first pass at CP-00 assumed a TypeScript/Node
backend (Fastify) based on the assignment's own TypeScript contract
sketch, before the actual project constraints (Python/FastAPI, one-day
timeline) were given.

**Review:** Rejected once the real stack and time budget were specified.
Not a code defect (no code had been written yet), but a reminder that an
AI's default architecture leans toward whatever language a spec example
happens to be written in, not toward the team's actual stack.

**Final:** CP-00 was redone in full against the fixed stack (React+TS+Vite,
Python+FastAPI, one-day scope), including re-deriving the persistence,
tenant-strategy and provider-registry recommendations for Python instead
of translating the TypeScript ones.

**Decision:** Claude's default embedding-provider recommendation was
OpenAI `text-embedding-3-small`, made before checking which API keys were
actually available.

**Review:** Once it was established that only an Anthropic key is on hand
right now, Claude flagged (rather than silently assumed) that Gemini and
OpenAI adapters would need to be fixture-tested initially, and that the
embedding-provider choice depends on a key we don't yet have.

**Final:** Decision explicitly deferred to the user; OpenAI stays the
config default (it's just an env var, zero code impact either way), but
CP-05 will use fixtures for the embedding path until a key exists.

## CP-01 -- Project foundation

**Decision/incident:** While provisioning a local PostgreSQL instance
(installed via `winget` after discovering the dev machine had no Docker),
Claude attempted several ways to set a known password on the freshly
installed cluster: (1) temporarily flipping `pg_hba.conf` to `trust` --
correctly blocked by the sandbox's own security guardrails as an
auth-weakening action; (2) inspecting the `winget` package manifest for
install-time password flags -- also blocked; (3) using Postgres's
official single-user recovery mode to create an app role directly. The
third attempt ran while a `Stop-Service` call had silently failed
(non-admin session), so the single-user process and the still-running
service briefly touched the same data directory concurrently. On the
retry, this left the WAL in a state Postgres could not recover from
(`PANIC: could not locate a valid checkpoint record`).

**Review:** This was a real mistake, not a hypothetical one -- it
corrupted a local dev-only Postgres cluster (no data of any value was in
it). Two of Claude's three recovery attempts were correctly refused by
the environment's own safety checks before they could weaken
authentication; the third was technically sound in isolation (single-user
mode is the standard, documented recovery path) but unsafe given the
concurrent-service risk that the failed `Stop-Service` call should have
been treated as a stop signal for the whole approach, not a reason to
retry via a different angle. After the corruption and a further blocked
uninstall attempt, Claude stopped attempting further infrastructure
workarounds and handed the remaining step back to the user with an exact,
minimal, one-time elevated command, rather than continuing to guess at
sandboxed workarounds.

**Final:** Postgres reinstalled with a password set correctly at install
time in one step; no further single-user-mode or config-editing tricks.
Lesson applied going forward: once one recovery path fails partway, stop
and hand control back rather than trying a second, riskier angle against
the same live resource.

**Decision:** CP-00 had planned Postgres + pgvector via
`docker compose up`.

**Review:** The actual dev machine has neither Docker nor WSL installed,
and installing them mid-build risked a Windows reboot the one-day budget
couldn't absorb. Flagged to the user rather than silently switched to
SQLite or silently attempted a Docker install.

**Final:** User chose native PostgreSQL + a hand-rolled (Python-side)
vector store for RAG later, keeping the stronger row-level-security
tenant story from CP-00 intact. Documented in `docs/DESIGN.md`.

**Accepted as generated, no correction needed:** the FastAPI app
structure and the `TenantContext` / `tenant_connection` /
`TenantScopedRepository` pattern were used as Claude wrote them. (The
migration SQL and test suite built on this pattern *did* need the two
corrections below, found once they were actually run against a live
database rather than only read.)

### CP-01 verification pass (run separately from the code that was reviewed)

**Incident:** The corrupted-Postgres-cluster failure above recurred a
second time on this same machine, independently of the single-user-mode
incident already documented (the Postgres log showed the identical
`PANIC: could not locate a valid checkpoint record`, from an unclean
shutdown, not from any action Claude took this time). Claude read the
Postgres log directly rather than guessing, confirmed the failure mode,
and -- consistent with the lesson recorded above -- did not attempt any
in-place recovery. It handed the user an exact reinstall procedure
instead, which resolved it.

**Decision:** The RLS policy on `notes` assumed
`current_setting('app.tenant_id', true)` returns `NULL` whenever
`app.tenant_id` is unset, per the comment Claude itself wrote in
`002_notes.sql` and `pool.py`.

**Review:** Once the tenant-isolation tests actually ran against a live
database (blocked until the Postgres incident above was resolved), the
`test_connection_without_tenant_context_sees_nothing` test failed --
not with an assertion failure, but with a raised Postgres error,
`invalid input syntax for type uuid: ""`. Claude reproduced this
directly in `psql` rather than guessing: a custom GUC that has been set
via `SET LOCAL` even once on a pooled connection does **not** revert to
`NULL` when that transaction ends or on `RESET ALL` (which asyncpg runs
on every connection release) -- it becomes `''`. The original comment's
premise was only true for a connection that had *never* been touched,
which in a pooled application is not the case after the first request.
Casting `''::uuid` raises rather than failing closed to zero rows.

**Final:** Policy changed to
`NULLIF(current_setting('app.tenant_id', true), '')::uuid` in both
`USING` and `WITH CHECK`, normalizing "never touched" and "touched, then
reset" to the same `NULL` result. Comments in both files updated to
describe the actual, tested Postgres behavior instead of the assumed
one. The security property (isolation) was never actually broken by
this -- the bug was in what a boundary condition returned (an error)
versus what the design intended (an empty result) -- but it would have
surfaced as a confusing 500 the first time any code path acquired a raw
pooled connection.

**Decision:** The `pool` pytest fixture was session-scoped, based on a
comment claiming async fixtures "must share one event loop for the whole
session."

**Review:** Running the test suite against a live database (also blocked
until the Postgres incident was resolved) failed every tenant-isolation
test with `RuntimeError: ... Future ... attached to a different loop` --
pytest-asyncio's per-test function scope for test items doesn't
automatically follow a session-scoped fixture's loop scope without
matching config, which this project's pytest-asyncio version doesn't
support via ini option.

**Final:** Made the `pool` fixture function-scoped instead (migrations
and dev-tenant seeding are already idempotent, so the added per-test
cost is negligible for six tests), and set
`asyncio_default_fixture_loop_scope = "function"` explicitly rather than
relying on an implicit default.

## CP-02 -- Provider-neutral contracts, registry, model configuration

**Decision:** Before writing `models.yaml`, Claude fetched Anthropic,
OpenAI, and Gemini pricing pages rather than using training-data pricing,
per the checkpoint's explicit instruction not to fabricate or guess
current numbers.

**Review:** The first Gemini fetch (`ai.google.dev/gemini-api/docs/pricing`,
via the WebFetch tool, which summarizes fetched pages through an
intermediate model) returned model names that looked suspicious on their
face -- "Gemini 3.8 Flash," "Gemini 3.1 Pro Preview" -- a version-number
jump that could plausibly be either real (this session's own knowledge
cutoff is January 2026, eight months before the date this checkpoint was
built) or a hallucination introduced by the fetch tool's summarization
step. Claude treated this as unverified rather than either accepting or
discarding it: ran two independent `WebSearch` queries (which return raw
result snippets, not an LLM-paraphrased summary) for Anthropic and Gemini
pricing separately, then fetched the primary-source pages directly
(`platform.claude.com/docs/en/about-claude/pricing`,
`ai.google.dev/gemini-api/docs/pricing.md.txt`) rather than relying on any
single summarized fetch. The Anthropic model ids that came back
(`claude-sonnet-5`, `claude-opus-5`, `claude-haiku-4-5-20251001`,
`claude-fable-5-1`) were independently checkable against this session's
own system-provided model identity, which matched exactly -- real,
external corroboration, not just internal consistency between two AI
outputs. The Gemini version numbers held up the same way across
independently-fetched sources.

**Final:** Used the corroborated pricing/model-id data in `models.yaml`,
documented the exact sourcing (URLs + date) in `docs/PROVIDER_NOTES.md`,
and left the small number of facts that did *not* corroborate cleanly
(Gemini's `gemini-2.5-flash` max output token count, Gemini embedding
pricing) as explicit `null`/TBD rather than picking whichever fetched
number seemed most plausible.

**Bug (self-caught, not user-flagged):** The first run of
`tests/providers/test_extensibility.py` failed both generic-resolution
tests with `ProviderNotFoundError`. Cause: the test fixture
(`fixture_models.yaml`) configured `test-chat-model`'s `provider:` as
`"test-provider"`, but `FakeProvider.id` (the id it gets registered under
in `ProviderRegistry`) is `"fake"` -- a mismatch between two pieces of
test scaffolding written in the same pass, not a design flaw in
`ModelRegistry`/`ProviderRegistry` themselves. Caught immediately by
running the tests rather than assuming they'd pass because the code
"looked right"; fixed by aligning the fixture's `provider:` value to
`"fake"`. Recorded because it's a concrete instance of the same pattern
as CP-01's RLS bug: code that is correct by inspection can still be wrong
in a way only running it reveals.

**Design decision, not a correction:** `Provider.embed()` is a concrete
method with a default that raises `ProviderError(kind=UNSUPPORTED)`,
rather than an abstract method every adapter must implement, or a
separate `EmbeddingProvider` protocol. This was evaluated against the two
alternatives the checkpoint's own instructions raised, not proposed and
then reversed -- recorded here because the tradeoff (documented in
`docs/DESIGN.md`) is a real architectural choice worth being able to
defend, not because anything was rejected.

## CP-03 -- Anthropic, Gemini, and OpenAI adapters

**Decision:** Rather than trust training-data knowledge of each SDK's
streaming event names, exception hierarchy, and tool-call shapes (all
things that change between SDK major versions), Claude installed the real
pinned packages (`anthropic==1.7.0`, `openai==3.17.0`, `google-genai==2.24.0`)
early and read their own source directly (`_exceptions.py`, the
`openai/types/responses/*.py` type files, `google/genai/types.py`) for
every field name and class this checkpoint's adapters depend on, rather
than relying only on fetched documentation pages.

**Review:** This caught real gaps a docs-only pass would likely have
missed or gotten subtly wrong: Anthropic's exact exception class list (9
distinct classes with the two-tier `PermissionDeniedError`/`AuthenticationError`
split for 401 vs 403, not the generic pattern the docs page describes in
prose); OpenAI's `ContentFilterFinishReasonError`/`LengthFinishReasonError`
existing as real classes that are nonetheless *wrong* to catch in this
adapter's error path (they're raised by a structured-output helper this
adapter doesn't call); and, most concretely, that Gemini's SDK explicitly
marks fragment-level tool-argument streaming ("`FunctionCall.partial_args`...
This field is not supported in Gemini API") -- a fact load-bearing for
`GeminiAdapter.stream()`'s behavior that no amount of generic "Gemini has
function calling" documentation would have surfaced without reading the
type definitions themselves.

**Final:** All three adapters' streaming/error/tool-call logic is sourced
from and cross-checked against the installed SDK's own code, cited by file
in each adapter's module docstring and in `docs/PROVIDER_NOTES.md`.

**Incident (self-caught, not user-flagged):** The first run of
`test_anthropic_adapter.py`'s error-mapping tests failed six of seven
cases with `ProviderErrorKind.UNKNOWN` instead of the expected specific
kind. Cause: constructing a fake `anthropic.RateLimitError` (etc.) with
`response=SimpleNamespace(headers={})` doesn't actually raise
`RateLimitError` -- the exception class's own `__init__` reads
`response.status_code` and `response.request`, neither present on the
bare fake, so constructing it raised an unrelated `AttributeError` instead
(which the adapter correctly, if unhelpfully, translated to `UNKNOWN` --
technically correct behavior exposing an incorrect test double). Claude
diagnosed this by reading `anthropic.APIStatusError.__init__`'s actual
source rather than guessing, then fixed every affected test fixture to
build a real `httpx.Response` (real status code, real request) instead of
a hand-rolled stand-in.

**Incident (self-caught, not user-flagged):** A first draft of the OpenAI
streaming test used `event.item_id` at the `response.output_item.added`
event, which doesn't carry that field (confirmed by re-reading
`response_output_item_added_event.py`: it has `.item`, `.output_index`,
not `.item_id`) -- an editing slip introduced while writing the adapter
itself, not a bad assumption about the API. Caught and fixed before the
test suite was ever run clean, by re-checking the source file the
implementation was supposedly already based on.

**Incident (self-caught, not user-flagged):** A test helper
(`_part(**kwargs)` in `test_gemini_adapter.py`) passed `text=None,
function_call=None` as `SimpleNamespace` defaults alongside `**kwargs`
that could also contain `text=` or `function_call=`, raising `TypeError:
got multiple values for keyword argument` the moment any test actually
supplied one. Caught immediately by running the tests; fixed with
`kwargs.setdefault(...)` instead of colliding positional defaults.

**Accepted as generated, no correction needed:** the per-provider
reconciliation decisions documented in `docs/PROVIDER_NOTES.md` and
`docs/DESIGN.md` (Gemini's tool-result name lookup via request-scoped
history rather than a CP-02 contract change; OpenAI's `item.id` vs.
`item.call_id` correlation map; the three adapters' differently-shaped
`_translate_error` methods) were designed and implemented as described,
without a rejected first attempt -- each was informed directly by reading
the installed SDK source before writing the corresponding adapter code,
rather than being written first and corrected after.

**Explicitly not done, and why:** no adapter was exercised against a real
provider API in this checkpoint -- all three are fixture-tested only (see
`docs/PROVIDER_NOTES.md`'s "Live- vs fixture-tested" note). This wasn't a
credential-availability problem this time (CP-00 already flagged that
possibility); it's simply what CP-03's own instructions call for ("Tests
must NOT depend on paid/live APIs"). Recorded so this isn't mistaken for
an oversight.

## CP-04 -- Conversation persistence, real streaming, cancellation, minimal UI

CP-04's manual end-to-end verification step (STEP 32) is what this
section is mostly about -- three real, load-bearing bugs surfaced only by
actually running the app against a live Anthropic key, none of which any
fixture/unit test in this codebase (including ones written specifically
to test the affected code) could have caught. All three are described in
full, with the actual mechanism, in `docs/DESIGN.md`; this section is the
narrative of finding them, not a duplicate of the technical explanation.

**Incident 1 -- raw provider response body reaching the browser.** The
first live request (an intentionally-wrong placeholder key a human
pasted in, format `apikey_...`, not Anthropic's `sk-ant-...`) returned a
401. The SSE `error` event's `message` field contained the *entire* raw
Anthropic response body: `"Error code: 401 - {'type': 'error', 'error':
{'type': 'authentication_error', 'message': 'invalid x-api-key'},
'request_id': 'req_...'}"`. Root cause: every CP-03 adapter's
`_translate_error` used `message=str(exc)`, and `str()` on this SDK's
exceptions embeds the literal response body the SDK was constructed from
-- confirmed by reading `anthropic/_base_client.py`, which builds the
exception's message as `f"Error code: {status_code} - {body}"`. This is
exactly the class of leak CP-04's own instructions (STEP 15) call out by
name ("Do NOT send: ... raw response bodies containing sensitive
information") -- it existed in CP-03 code for a full checkpoint before
this one's live test surfaced it, because CP-03's fixture tests only ever
constructed exceptions with short, hand-written messages like `"bad
key"`, never a realistic SDK-formatted one.

**Review:** fixed in all three adapters by introducing
`app/providers/errors.py::safe_message(kind)` -- a fixed, generic,
kind-keyed string -- and having each adapter log the real exception
server-side (`logger.warning`) instead of forwarding it. A regression
test (`test_error_message_never_leaks_the_raw_provider_response_body`)
reconstructs the exact SDK-internal message-building pattern (not a
hand-simplified one) to make sure this specific mistake can't quietly
come back.

**Incident 2 -- internal model id sent upstream instead of the
provider's own.** Once the key was corrected (a real one, provided after
being asked where to find it) and had credits, the very first real
request returned a **different**, more fundamental error: `404 model:
claude-sonnet`. `ChatService.prepare_turn` had been building
`CompletionRequest(model=model_id, ...)` using the *internal* id
throughout, while every CP-03 adapter was written assuming that field
already held the resolved `provider_model_id` by the time it arrived --
an assumption CP-02's own docstring for the field directly contradicted
("never a provider's own model string"), and nothing had ever noticed
the contradiction because no `CompletionRequest` had reached a real
provider before this checkpoint's manual test.

**Review:** this was a full application-breaking bug -- no request to
any configured provider could ever have succeeded until it was fixed,
and no unit test in the suite (all built on fake/scripted providers that
don't validate the model string at all) was capable of catching it. Fixed
in `ChatService.prepare_turn` (`CompletionRequest.model =
model_config.provider_model_id`, not the internal id), and the
contradictory CP-02 docstring was corrected to describe the shipped,
working behavior rather than the original, untested intent. A regression
test (`test_prepare_turn_resolves_provider_model_id_not_internal_id`)
locks in the resolution even though, like all the fake-provider tests, it
can't independently prove a real provider accepts the result -- that
proof is what the live test itself provided.

**Incident 3 -- cancellation silently persisted nothing.** With
streaming genuinely working, Stop was tested next by force-closing a
`curl` connection mid-generation. The existing unit test for this exact
path (`test_stream_reply_persists_interrupted_message_on_cancellation`,
which cancels a plain `asyncio.Task` against a hanging fake provider) had
been green the entire time -- and yet the real conversation had zero
assistant messages afterward, not even an interrupted one, both
immediately and after waiting several seconds to rule out "still running
in the background."

**Review:** diagnosed with temporary logging rather than guessed at --
the log showed the cleanup `INSERT`'s own `await` raising
`CancelledError("Cancelled via cancel scope ... by <Task ...
run_asgi()>")`, i.e. Starlette's `StreamingResponse` cancels via an
*anyio cancel scope*, which (unlike a bare `asyncio.Task.cancel()`, what
the passing unit test used) keeps re-raising `CancelledError` at *every*
subsequent `await` in the same task until the scope itself exits --
including the cleanup write, aborting it mid-`INSERT`. The unit test
couldn't have caught this: it doesn't go through Starlette at all, so it
never exercises anyio's cancel-scope semantics, only plain asyncio's
"cancel once" behavior.

**Final:** wrapped the cleanup persist in `asyncio.shield()`. Verified
with the same temporary logging that `await shield(...)` still raises
`CancelledError` back to the caller immediately (correct, expected
`shield()` behavior, not a bug) while the shielded write itself keeps
running detached and does complete -- confirmed by checking the
conversation again ~2-5 seconds after disconnecting and seeing the
interrupted row with the exact partial text that had streamed before the
cut. Diagnostic logging was removed once the fix was confirmed; the
finding is preserved in `docs/DESIGN.md`'s "Cancellation" section instead
of left only in this log.

**A note on how these were found, since it's the actual lesson:** all
three were caught by literally running the app against a real provider
and a real disconnect, not by writing more fixture tests, not by
re-reading the code more carefully, and not by reasoning about asyncio
semantics from first principles (the first cancellation fix attempt,
`asyncio.shield()`, was applied on sound theoretical grounds *before*
being verified, and initial verification looked like it hadn't worked
until the actual timing was checked correctly -- confirming it live,
rather than trusting the theory, is what caught that near-miss too).
Every one of these bugs would have shipped invisibly in a submission that
only ran the (extensive, and otherwise accurate) automated test suite.

## CP-05 -- RAG, document upload, retrieval, grounded citations

**Decision, deviating from the checkpoint's own suggested design:** the
checkpoint's instructions describe a new `EmbeddingProvider` abstraction
(`embed_documents`/`embed_query`). Claude built `EmbeddingService` as a
thin wrapper over the *existing* `Provider.embed()` method instead --
CP-02 already defined that method on the core `Provider` interface,
specifically anticipating an embedding-capable adapter overriding a
default that raises `UNSUPPORTED`. Building a second, parallel
provider-resolution mechanism would have duplicated
`ProviderRegistry`'s credential-driven availability and `ProviderError`
normalization for no real benefit. This is a considered deviation, not an
oversight -- documented in `docs/DESIGN.md` and `docs/PROVIDER_NOTES.md`
with the reasoning, in case it's asked about directly.

**Self-caught bug 1 (found via test flakiness, not immediately):** the
first full test-suite run after adding CP-05 code failed exactly one
test -- chunk ordering came back reversed (`[3,2,1,0]` instead of
`[0,1,2,3]`) -- despite that same test passing when run in isolation
moments earlier. Root cause: `ChunkRepository.list_for_collection_with_filenames`
had no `ORDER BY` clause at all, so Postgres made no row-order guarantee;
it happened to come back in insertion order against an empty table and
stopped doing so once the table held rows from other tests too. Fixed by
adding an explicit `ORDER BY document_id, chunk_index` -- correct
practice regardless (retrieval re-sorts by similarity afterward anyway,
so this was always latent, just invisible until the table had enough
data in it to expose Postgres's actual, undefined ordering).

**Self-caught bug 2, found the same way, immediately after:** the fix for
bug 1 also surfaced that the same "worked in isolation, not against a
fuller database" pattern had left ~340 rows of accumulated test data
(collections, chunks) sitting in the real dev database -- the test
suite's autouse cleanup fixture (`tests/conftest.py`) truncated
`notes`/`messages`/`conversations` (CP-01/04) but was never extended to
include the three new CP-05 tables. Not a functional bug (each test uses
its own randomly-generated UUIDs, so cross-test pollution never produced
a false pass or fail), but genuine hygiene debt -- fixed by adding
`chunks, documents, collections` to the same `TRUNCATE` statement, and
the accumulated rows were cleared out of the dev database directly
(disposable, entirely test-generated data).

**Preventive fix applied proactively, not reactively:** `EmbeddingService`
was written from the start to resolve its provider *lazily* (inside
`embed_documents`/`embed_query`, not in `__init__`) rather than eagerly,
specifically because CP-04 had already shown what happens when a
provider-backed service resolves eagerly at construction time: it
crashes application *startup* the moment that provider's API key is
unset, not just the feature that needed it. `app/main.py` constructs
`EmbeddingService` unconditionally, the same way it constructs
`ChatService` -- an eager `provider_registry.get("openai")` inside
`EmbeddingService.__init__` would have reintroduced exactly the failure
mode CP-03/04's wiring (`build_provider_registry`) was built to prevent.
Caught by applying the earlier lesson before writing the code, not by
hitting the crash first.

**Self-caught bug 3 (found immediately via test failures, not live):** a
test fixture (`FakeEmbeddingProvider`, written for these tests only) used
`hashlib.sha256(text).digest()[:dimension]` to produce deterministic fake
vectors -- but SHA-256 digests are only 32 bytes, so requesting a
1536-dimension vector (the real, schema-enforced size) silently returned
only 32 elements, tripping the `chunks_embedding_dimension` CHECK
constraint the moment any test used the real dimension instead of a
toy one. Fixed by seeding a `random.Random` instance from the hash
instead of slicing it directly, which can produce a vector of any
requested length deterministically.

**Manual verification finding, live:** with no `OPENAI_API_KEY`
configured, a real document upload against the running app returned a
clean `{"status": "failed", "error": "no embedding provider is
configured"}` -- but only after `IngestionService.ingest()` was
specifically checked and found to catch `ProviderError` (a configured
provider failing) without also catching `ProviderNotFoundError` (no
provider configured at all, a different, unrelated exception hierarchy
from CP-02/03). Without that second `except` clause, an unconfigured
embedding provider would have produced an uncaught exception reaching
the route as a generic 500, not the deliberate `failed`-status document
STEP 14 calls for. Fixed before this was ever exercised by a real
request with a missing key; confirmed by then actually removing the key
and uploading a real file against the live server.
