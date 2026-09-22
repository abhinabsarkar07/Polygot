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
structure, the `TenantContext` / `tenant_connection` /
`TenantScopedRepository` pattern, the row-level-security migration SQL,
and the tenant-isolation test suite were used as Claude wrote them.
