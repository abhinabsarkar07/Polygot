# AI Usage

## Overview

This project was built with Claude Code as an implementation and testing
assistant, working through a fixed set of checkpoints (CP-00 through
CP-07) with my explicit authorization required before each one began. I
set the architecture constraints, requirements, and verification
standards up front (fixed stack, a provider-neutral core with no
per-provider branching, structural multi-tenancy, no provider-abstraction
frameworks); Claude Code produced the implementation, tests, and
documentation against those constraints, and I reviewed and gated
progress at every checkpoint rather than letting it run end-to-end
unsupervised. This disclosure is required by the assignment and is not
minimized here.

## Tools Used

Claude Code (Claude Sonnet 5). No other AI coding tool was used.

## Where AI Assistance Was Used

| Area | AI assistance | Engineering responsibility |
| --- | --- | --- |
| Project setup | FastAPI/React scaffolding, migrations | Fixed the stack, the no-Docker call, the multi-tenancy requirement |
| Provider adapters | Implementation against each SDK | Required sourcing from the *installed* SDK, not memory; provided real keys; set the error taxonomy |
| Streaming / cancellation | SSE implementation, cancellation plumbing | Required live verification against a real key before accepting the checkpoint |
| RAG | Ingestion/chunking/retrieval/citation implementation | Set tenant-scoping and server-authoritative-citation requirements; no-OCR stated honestly, not implied |
| Observability / retry / fallback | Usage tracking, cost calc, retry/fallback logic | Specified the streaming-retry-safety rule before implementation started |
| Testing | Test scaffolding and edge-case coverage | Required adversarial cross-tenant tests, real-database RLS tests, honest full-suite results |
| Documentation | Drafting and restructuring | Required every correction be real and evidenced; required this disclosure; directed this rewrite |
| Debugging | Diagnosis and fixes for issues found in testing | Required live verification specifically because it was expected to surface what fixtures couldn't |

## Engineering Decisions

These are decisions I made, not ones Claude proposed and I passively
accepted:

- **Scope.** Prioritized a correct, tested core (provider abstraction,
  streaming, persistence, tenant isolation, RAG, observability/resilience)
  over attempting every optional feature. Tool execution (Module D) was
  explicitly cut rather than half-built once the time box for CP-06 made
  clear it would come at the cost of finishing documentation and
  verification properly.
- **Stack.** React + TypeScript + Vite, Python + FastAPI, PostgreSQL --
  fixed by me before CP-00 began. When the first requirements pass
  defaulted toward a Node backend (matching the assignment's own
  TypeScript contract sketch), I corrected it to the actual stack rather
  than letting it stand.
- **No Docker/pgvector.** The actual dev machine had neither Docker nor
  WSL, and installing them mid-build risked time the one-day budget
  couldn't absorb. I chose native PostgreSQL with a hand-rolled
  Python-side vector store over spending build time on infrastructure
  setup, keeping the row-level-security tenant model intact either way.
- **Provider abstraction, no framework.** I required a provider-neutral
  core with zero `if provider == "x"` branching and explicitly ruled out
  LangChain/LiteLLM-style abstraction libraries, since hiding that
  translation logic behind a framework would have hidden exactly the
  design work the project is meant to demonstrate.
- **Multi-tenancy as structural, not conventional.** I required tenant
  isolation be enforced by the database (row-level security), not by an
  application-level filter a future engineer could forget to add --
  stated as a hard requirement from the first checkpoint, not decided
  after the fact.
- **Retry/fallback safety boundary.** I specified directly that retry and
  fallback must never happen once real output has already streamed to
  the browser, before that logic was implemented -- this wasn't a
  correction after the fact, it was a stated constraint the
  implementation had to satisfy.
- **Cached-token cost pricing.** Rather than accept a guessed pricing
  rule for an ambiguous case (providers report cached-token accounting
  differently, and it was never confirmed live), I required the gap be
  left unpriced and documented rather than resolved by assumption.

## Human Verification

The following was verified during development, with results required
and reviewed before the corresponding checkpoint was authorized to
proceed -- not accepted from a generated summary:

- Full backend test suite (246 tests as of the last checkpoint) run and
  passing, including adversarial cross-tenant isolation tests against a
  real PostgreSQL instance with row-level security enabled.
- Frontend TypeScript build, production build, and lint run clean.
- Real, live conversations against the Anthropic API: streamed replies,
  mid-generation cancellation reaching the upstream connection, an auth
  failure, and (in the final verification pass) real per-turn cost and
  latency tracking with tenant-scoped aggregation confirmed against the
  actual running application, not just its test suite.
- Safe failure-mode behavior checked directly against the running app:
  an invalid model id, a provider with no credential configured, a
  cross-tenant resource id, and an unsupported upload type each produce
  the intended clean error, not a crash or a leak.
- A stale backend process (running code from before the observability
  checkpoint) was caught specifically *by* this live verification step --
  a static code/test review would not have caught it -- and fixed before
  being reported as working.

## Areas Requiring Careful Review

- **Provider APIs** -- three different exception hierarchies and tool-
  call streaming granularities; docs and shipped SDK behavior didn't
  always agree, so adapter code needed checking against actual installed
  source.
- **Streaming** -- partial output and cancellation raise real
  state-management questions (what's safe to retry once shown) with no
  single obviously-correct default.
- **Tenant isolation** -- a query that looks correctly scoped can still
  leak if the enforcement boundary is in the wrong place; needed
  verifying against the database's actual behavior, not just the SQL.
- **RAG** -- retrieval quality, citation mapping, and tenant/collection
  filtering are application-level correctness questions, not something
  one API call gets right by default.
- **Cost** -- usage fields and pricing dimensions differ across
  providers (`None` vs. genuinely zero, cached-token semantics);
  assuming uniformity would have produced a confidently wrong number.
- **Security** -- generated code can look reasonable while still
  missing validation or letting a raw upstream error reach a client;
  each needed deliberate checking, not a read-through.

## What I Did Not Delegate Blindly

Provider API behavior (verified against installed SDK source, not
memory); authentication and secrets (real keys never committed, per a
full git-history scan; the unsigned tenant header is a disclosed,
assignment-permitted simplification, not an oversight); tenant isolation
(structural from the first checkpoint, adversarially tested); cost
calculations (an ambiguous pricing case was left unpriced rather than
guessed); retry/fallback behavior (the safety boundary was a stated
requirement before implementation, then tested against attempts to
break it); security-sensitive validation (a raw-error leak was treated
as a real defect, not cosmetic); and final feature claims (every
checkpoint had to state exactly what was live-tested vs. fixture-tested
vs. untested -- the final verification pass caught and fixed a
stale-process issue rather than trusting an earlier report).

## AI vs. Engineering Ownership

```
requirements, architecture constraints, verification standards (mine)
        ↓
Claude Code: implementation, tests, debugging suggestions
        ↓
review against requirements, live verification, test results
        ↓
accept, correct, or reject -- checkpoint authorized to proceed, or not
        ↓
final implementation
```

Not every generated suggestion was accepted as-is -- see "Corrections and
Adaptations" below for the ones that weren't, and "Engineering Decisions"
above for the constraints that shaped what Claude Code produced in the
first place.

## Corrections and Adaptations

Real, verifiable corrections from development -- not an exhaustive log
(the incremental commit history, one per checkpoint, is the record of
what actually shipped when):

- An initial requirements pass assumed a Node/TypeScript backend,
  matching the assignment's own contract sketch; corrected to the actual
  fixed stack before any code was written.
- Adapter code was built from each provider's *installed* SDK source
  rather than assumed SDK shape, after an early pricing lookup returned
  suspicious, unverified model names that needed cross-checking against
  independent sources before being trusted.
- Three real, load-bearing bugs were found only by live-testing against
  a real Anthropic key, not by the (otherwise extensive) automated test
  suite: a raw provider error body reaching the browser, the internal
  model id being sent upstream instead of the provider's own id, and
  cancellation silently persisting nothing due to `anyio`'s cancel-scope
  semantics. Mechanism for each is in `docs/DESIGN.md`.
- Citation metadata was made server-authoritative (the model only emits
  a bracketed id) rather than trusting model-reported filenames/pages,
  to make fabricated citation metadata structurally impossible.
- A shared test-cleanup fixture missing a newly-added table caused a
  suite-wide false-failure signature twice, in two different checkpoints
  -- recorded as a recurring pattern, not treated as learned after the
  first instance.

## Limitations

AI assistance accelerated implementation but did not replace
understanding of the submitted code, deciding what correct behavior
looks like, or verifying it. Every architectural decision in
`docs/DESIGN.md`, every provider-specific fact in
`docs/PROVIDER_NOTES.md`, and every claim in this file is expected to be
explainable and defensible in a live technical discussion --
`docs/INTERVIEW_NOTES.md` was prepared as concrete preparation for that,
not generated as a substitute for it. This file is a factual record of
how the project was built, not a claim that every line was typed by
hand, and not a claim that AI made the engineering decisions on its own.
