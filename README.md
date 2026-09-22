# Polyglot

A multi-provider AI chat workbench: pick Anthropic, Gemini, or OpenAI per
turn behind one normalized internal contract, stream real token-by-token
replies over SSE, ground answers in your own uploaded documents with
server-authoritative citations, and see per-turn cost/latency with
automatic retry and fallback when a provider hiccups -- all of it
structurally isolated per tenant at the database level, not just by
convention.

## What Is Implemented

- **Chat**: persistent conversations, real streaming (not simulated),
  mid-generation cancellation that reaches the upstream provider
  connection, provider/model switching turn-to-turn with coherent history.
- **Providers**: Anthropic, Gemini, and OpenAI behind one normalized
  contract, zero provider-name branching in application code (verified,
  not just claimed -- see "Architecture").
- **RAG**: document upload (.txt/.md/.pdf) into collections, configurable
  chunking, embeddings, tenant/collection-scoped retrieval, grounded
  generation with server-authoritative citations, a deterministic
  "no evidence" path that never calls the model unnecessarily.
- **Observability**: TTFT, latency, token counts, and cost per turn;
  tenant-scoped `GET /api/usage/summary`.
- **Resilience**: retry with exponential backoff (eligible errors only),
  config-driven fallback chains, per-attempt timeouts -- all gated by an
  explicit safety rule: never retry or fall back once real output has
  streamed to the browser.
- **Multi-tenancy**: PostgreSQL row-level security enforces tenant
  isolation on every tenant-owned table; not an application-level filter
  that could be forgotten.

**Not implemented:** tool execution (Module D -- see "Scope / Assignment
Status"), per-tenant rate limiting, semantic caching, hybrid search,
reranking, an evaluation framework, OCR.

## Architecture

```
React/Vite  --HTTP/SSE-->  FastAPI  --X-Tenant-Id-->  Postgres (RLS)
                               |
                          ChatService / RAG
                               |
                          ModelRegistry (models.yaml)
                               |
                          ProviderRegistry
                          /      |       \
                  Anthropic   Gemini    OpenAI
                  Adapter     Adapter   Adapter
```

Application code (chat orchestration, RAG, API routes) depends only on a
normalized `CompletionRequest`/`StreamEvent`/`Provider` contract -- never
on a provider SDK type or a provider-name branch. Full diagram, request
flow, and the reasoning behind every major decision are in
`docs/DESIGN.md`.

## Tech Stack

- React + TypeScript + Vite (frontend)
- Python + FastAPI (backend)
- PostgreSQL 16 with row-level security (persistence + hand-rolled vector
  store -- no pgvector; see `docs/DESIGN.md`, "Design Decisions")
- Provider SDKs: `anthropic`, `google-genai`, `openai`

## Providers

| Provider | Adapter | Streaming | Tools Normalized | Live Tested |
| --- | --- | --- | --- | --- |
| Anthropic | `anthropic_adapter.py` | Yes | Yes | **Yes** -- real streamed reply, real mid-generation Stop, real auth failure |
| Gemini | `gemini_adapter.py` | Yes | Yes | No -- fixture-tested only |
| OpenAI (chat) | `openai_adapter.py` | Yes | Yes | No -- fixture-tested only |
| OpenAI (embeddings) | `openai_adapter.py::embed()` | n/a | n/a | No -- only the "no key configured" failure path was exercised live; a real successful embedding call was never performed |

"Tools Normalized" means each adapter translates the provider's native
tool-call streaming events into `ToolUseStartEvent`/`ToolUseDeltaEvent`/
`ToolUseCompleteEvent`. No application-level tool execution loop exists
yet -- see "Scope / Assignment Status".

## Prerequisites

- **Node.js** 20+ and npm
- **Python** 3.11+. On Windows, if bare `python`/`pip` resolve to the
  Microsoft Store stub, use the `py` launcher instead (`py -m venv`,
  etc.), as the commands below do.
- **PostgreSQL 16**, running locally and reachable on `localhost:5432`.
- **At least one chat provider API key** (Anthropic, Gemini, or OpenAI)
  to send a message and see a real reply. Without one, the app still
  runs fully -- conversations, the model list, and tenant isolation all
  work -- but sending a message to an unconfigured provider returns a
  clean `503`, not a crash.
- **An OpenAI API key** specifically, to use RAG (embeds via
  `text-embedding-3-small`). Without it, uploads fail cleanly with
  `{"status": "failed", "error": "no embedding provider is configured"}`.

## Quick Start

From a clean clone, in order:

```bash
# 1. Environment
cp .env.example backend/.env
# edit backend/.env: set DATABASE_URL and whichever provider API key(s) you have

# 2. Database (see "Database Setup" below if PostgreSQL isn't installed yet)
#    Tables/RLS policies/dev tenant seed data are created automatically
#    on first backend start -- no separate migration command.

# 3. Backend
cd backend
py -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload
# -> http://localhost:8000 ; GET /api/health should return {"status": "ok"}

# 4. Frontend (separate terminal)
cd frontend
npm install
npm run dev
# -> open the printed URL, default http://localhost:5173
```

Total setup time on a machine with PostgreSQL already installed is well
under five minutes; installing PostgreSQL itself (see below) adds a few
more.

## Environment Variables

`.env.example` (repo root) lists every variable with a placeholder value
-- copy it to `backend/.env` and fill in real values there; `backend/.env`
is gitignored and never committed.

| Variable | Required | Notes |
| --- | --- | --- |
| `DATABASE_URL` | Yes | `postgresql://polyglot_app:<password>@localhost:5432/polyglot` |
| `FRONTEND_ORIGIN` | No | CORS origin, defaults to `http://localhost:5173` |
| `ANTHROPIC_API_KEY` | No | One of the three provider keys, each independent |
| `GEMINI_API_KEY` | No | " |
| `OPENAI_API_KEY` | No | Also required for RAG (embeddings) regardless of chat model |

Retry/timeout tuning (`RETRY_MAX_RETRIES`, `RETRY_BASE_DELAY_SECONDS`,
`RETRY_MAX_DELAY_SECONDS`, `PROVIDER_REQUEST_TIMEOUT_SECONDS`) and the
upload size cap (`MAX_UPLOAD_BYTES`) all have sane defaults (see
`backend/app/core/config.py`) and don't need to be set for local use.

## Database Setup

The app expects a dedicated, non-superuser Postgres role that owns its
own database -- this is what makes row-level security actually enforce
(see `docs/DESIGN.md`, "Multi-Tenancy").

**If PostgreSQL isn't installed yet** (Windows, via winget -- adjust for
your platform's package manager otherwise):

```powershell
winget install --id PostgreSQL.PostgreSQL.16 --silent `
  --accept-package-agreements --accept-source-agreements `
  --override "--mode unattended --superpassword <CHOOSE_A_SUPERUSER_PASSWORD> --unattendedmodeui minimal"
```

**Then create the app role and database** (run once; replace the password
with whatever you set above, and pick your own app password):

```powershell
$env:PGPASSWORD = "<THE_SUPERUSER_PASSWORD_FROM_ABOVE>"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -h localhost `
  -c "CREATE ROLE polyglot_app WITH LOGIN PASSWORD 'polyglot_dev_local_2026';"
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -h localhost `
  -c "CREATE DATABASE polyglot OWNER polyglot_app;"
```

Tables, row-level security policies, and dev tenant seed data (`tenant-a`,
`tenant-b`) are created automatically the first time the backend starts
(`app/db/migrate.py`, `app/db/seed.py`) -- there is no separate "run
migrations" step.

## Backend

```bash
cd backend
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

## Frontend

```bash
cd frontend
npm run dev
```

## Tests

Requires the database from "Database Setup" to be running (tests exercise
real row-level-security behavior, not mocks). No provider API key is
required -- all provider/adapter/embedding tests use fixtures, never a
live call.

```bash
cd backend
./.venv/Scripts/python.exe -m pytest -v
```

Frontend:

```bash
cd frontend
npm run lint   # oxlint
npm run build  # tsc -b && vite build -- typecheck + production build
```

## RAG

1. Create or select a **collection**.
2. **Upload** a `.txt`, `.md`, or `.pdf` file, with optional chunk
   size/overlap (defaults: 1000 chars / 150 overlap). Status goes
   `processing` -> `ready` or `failed` (with a reason -- a scanned PDF
   with no text layer fails honestly, it is not OCR'd).
3. Select the collection for a conversation; replies are answered only
   from that collection's evidence, streamed like ordinary chat, with a
   **Sources** list of clickable citation chips (`[S1]`, `[S2]`, ...)
   showing filename, chunk index, page (PDF only), similarity score, and
   the actual retrieved text.
4. **Top-K**/**similarity threshold** control retrieval per-query,
   independent of upload-time chunk size/overlap.
5. If nothing meets the similarity threshold, the reply is a
   deterministic "I don't know based on the provided documents." -- the
   model is never called in that case.

Embeddings: OpenAI `text-embedding-3-small` (1536 dimensions, $0.02/MTok),
reusing the same `Provider.embed()` interface chat already uses, not a
second abstraction. Vector storage: a plain Postgres `double precision[]`
column with cosine similarity computed in Python (no pgvector on this
install) -- tenant/collection scoping happens entirely in SQL before any
Python code sees a row. Full detail: `docs/DESIGN.md`, "RAG".

## Multi-Tenancy

Requests are scoped by an `X-Tenant-Id` header (`tenant-a`/`tenant-b`,
both seeded automatically). The header itself is unsigned and forgeable
by design, per the assignment's own guidance for a take-home -- what's
actually enforced is that once an identity is accepted, PostgreSQL
row-level security (forced, not just enabled) makes cross-tenant reads
and writes structurally impossible, adversarially tested, not just
asserted. Full detail, including exactly what a new engineer would have
to do to accidentally leak data (short answer: bypass the one connection
helper that sets the tenant session variable, which stands out in
review) is in `docs/DESIGN.md`, "Multi-Tenancy".

## Streaming

Real token-by-token streaming, not a simulated typewriter effect: the
backend forwards each provider event over SSE the instant it arrives
(`async for event in provider.stream(request): yield event`, all the way
to the HTTP response), and the frontend consumes it with `fetch` + a
hand-rolled SSE parser (`EventSource` can't POST, so it isn't used) that
correctly buffers across arbitrary network chunk boundaries. Full detail:
`docs/DESIGN.md`, "Streaming".

## Observability

Every completed or interrupted turn writes one usage record: provider,
requested vs. final model, TTFT, total latency, token counts, cost
(`Decimal`-precise), finish reason, retry count, fallback flag -- never
prompt/response text. `GET /api/usage/summary` returns a tenant-scoped,
per-provider rollup. Retry (eligible errors only, exponential backoff),
fallback (config-driven model chain), and per-attempt timeouts all
respect one non-negotiable rule: never retried or replaced once real
output has streamed to the browser. Full detail: `docs/DESIGN.md`,
"Observability" and "Retry and Fallback".

## Scope / Assignment Status

| Module | Status |
| --- | --- |
| Providers (Anthropic + Gemini + OpenAI, normalized contract) | **DONE** |
| Streaming chat (real SSE, cancellation, persistence, provider switching) | **DONE** |
| RAG (upload, chunking, embeddings, retrieval, grounded citations) | **DONE** |
| Tool calling | **CUT** -- provider-level tool-call event normalization exists in all three adapters (`ToolUseStartEvent`/`ToolUseDeltaEvent`/`ToolUseCompleteEvent`); no application-level tool registry or execution loop was built. See `docs/DESIGN.md`. |
| Observability, cost, retry/fallback/timeout | **DONE** |
| Multi-tenancy | **DONE** -- structural (RLS), adversarially tested |
| Security review | **DONE** -- see `docs/DESIGN.md`, "Security"; one disclosed gap (no per-tenant rate limit) |
| Per-tenant rate limiting / spend cap | **CUT** -- needs shared state (Redis or equivalent), out of scope for this take-home |
| Reranking, hybrid search, semantic caching, eval framework | **CUT** -- explicitly out of scope |
| OCR | **CUT** -- scanned PDFs fail extraction honestly rather than silently |

## Known Limitations

- **No OCR.** A scanned/image-only PDF fails extraction honestly
  (`status: "failed"`, a clear reason) rather than silently producing no
  chunks or pretending text was recovered.
- **Prompt-injection defense is real but not a guarantee.** Retrieved
  document content is delimited and framed as untrusted data with an
  explicit instruction not to follow embedded commands -- tested that
  this framing is actually present in what's sent, not that a model will
  necessarily obey it. See `docs/DESIGN.md`, "Prompt Injection".
- **The similarity threshold default (0.3) is a reasonable starting
  point, not a calibrated value** -- there's no evaluation dataset
  behind it.
- **Chunking is character-based, not token-based**, and context-window
  trimming's token counts are estimates (~4 chars/token) -- both stated
  as approximations, not exact.
- **No per-tenant rate limiting or spend cap.** Per-request cost is
  bounded (message length, output tokens, upload size), but nothing
  stops a tenant from sending unlimited requests. See `docs/DESIGN.md`,
  "Security".
- **Cached-token cost accounting is not implemented.** Reported but not
  priced into `cost_usd` -- see `docs/DESIGN.md`, "Observability".
- **Gemini and OpenAI chat adapters, and OpenAI embeddings, are
  fixture-tested only** -- not exercised against a real API in this
  project. See "Providers" above.
- **Tool calling is not implemented at the application level** -- see
  "Scope / Assignment Status".

## Design Documentation

- `docs/DESIGN.md` -- architecture, request flow, every major design
  decision (with rejected alternatives), multi-tenancy, security,
  observability, retry/fallback.
- `docs/PROVIDER_NOTES.md` -- concrete, sourced per-provider API
  differences (message format, streaming, tool calls, errors, pricing)
  and how each adapter reconciles them.
- `docs/AI_USAGE.md` -- how AI assistance was used, reviewed, and
  corrected during development.
- `docs/INTERVIEW_NOTES.md` -- prepared talking points and likely
  debugging questions for presenting this project.

## Repository Layout

```
polyglot/
├── frontend/
│   └── src/
│       ├── api/           client, conversations, models, collections, SSE parser
│       └── components/    Chat, MessageList, ModelSelector, ConversationList, CollectionPanel
├── backend/
│   ├── app/
│   │   ├── api/            HTTP routes (health, models, conversations, collections, usage)
│   │   ├── core/            settings, tenant context
│   │   ├── db/               pool, migrations, seed data
│   │   ├── providers/     provider-neutral contracts, registries, adapters
│   │   ├── repositories/  tenant-scoped data access (conversations, messages,
│   │   │                    collections, documents, chunks, usage)
│   │   ├── schemas/         request/response models
│   │   └── services/         chat orchestration, context-window trimming, extraction,
│   │                            chunking, embeddings, retrieval, RAG grounding, cost, retry
│   └── tests/
│       └── rag_fixtures/  hand-built minimal PDFs used by extraction/ingestion tests
├── docs/
│   ├── DESIGN.md
│   ├── PROVIDER_NOTES.md
│   ├── AI_USAGE.md
│   └── INTERVIEW_NOTES.md
├── .env.example
└── README.md
```
