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
