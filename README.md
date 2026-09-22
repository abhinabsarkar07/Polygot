# Polyglot -- Multi-Provider AI Workbench

**Status: CP-05.** A working end-to-end chat workbench with retrieval-
augmented generation: create a conversation, pick a model (Anthropic,
Gemini, or OpenAI -- whichever have API keys configured), send a message,
watch the reply stream in token by token, stop generation mid-stream,
switch providers between turns, reload without losing anything -- and
now, upload documents (PDF/TXT/Markdown) into a collection, select that
collection for a conversation, and get answers grounded in your own
documents with inline, inspectable citations. All of it is tenant-
isolated at the database level.

**Not yet built:** tool execution, retries/fallback, usage/cost
dashboards. See `docs/DESIGN.md` for what's planned and not yet started.

## Prerequisites

- **Node.js** 20+ and npm (frontend)
- **Python** 3.11+ (backend). On Windows, if the bare `python`/`pip`
  commands resolve to the Microsoft Store stub instead of a real
  interpreter, use the `py` launcher instead (`py -m venv`, etc.), as the
  commands below do.
- **PostgreSQL** 16, running locally and reachable on `localhost:5432`.
  No pgvector required -- see "RAG / vector storage" below for why.
- **At least one chat provider API key** (Anthropic, Gemini, or OpenAI)
  to send a message and see a real reply. Without one, the app still runs
  fully -- conversations, the model list, and tenant isolation all work --
  but sending a message to an unconfigured provider returns a clean
  `503`, not a crash.
- **An OpenAI API key** specifically, to actually use RAG (document
  upload embeds via `text-embedding-3-small`; see "RAG / embeddings"
  below). Without it, uploads fail cleanly with
  `{"status": "failed", "error": "no embedding provider is configured"}`
  -- not a crash, and not silently accepted.

## Database setup

The app expects a dedicated, non-superuser Postgres role that owns its
own database (this is what makes row-level security actually enforce --
see `docs/DESIGN.md`).

**If you don't have PostgreSQL installed yet** (Windows, via winget --
adjust for your platform's package manager otherwise):

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

Tables, row-level security policies, and dev tenant seed data are created
automatically the first time the backend starts (see `app/db/migrate.py`
and `app/db/seed.py`) -- there is no separate "run migrations" step.

## Environment setup

```bash
cp .env.example backend/.env
```

Edit `backend/.env` and set `DATABASE_URL` to match the role/password you
created above, e.g.:

```
DATABASE_URL=postgresql://polyglot_app:polyglot_dev_local_2026@localhost:5432/polyglot
```

Set whichever of `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `OPENAI_API_KEY`
you have -- each is independent; a provider with no key configured is
simply unavailable, not a startup error. `OPENAI_API_KEY` additionally
enables RAG document upload regardless of which chat model you use (see
"RAG / embeddings" below).

## Backend setup and run

```bash
cd backend
py -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
./.venv/Scripts/python.exe -m uvicorn app.main:app --reload
```

The API is now on `http://localhost:8000`. `GET /api/health` should
return `{"status": "ok"}`.

## Frontend setup and run

```bash
cd frontend
npm install
npm run dev
```

Open the printed URL (default `http://localhost:5173`).

## Using the chat UI

1. The **Tenant** field at the top defaults to `tenant-a` (a dev tenant
   seeded automatically -- `tenant-b` also exists, and has no access to
   `tenant-a`'s conversations, collections, or documents, or vice versa;
   see "Tenant identifier" below).
2. Click **+ New Conversation**.
3. Pick a model from the dropdown.
4. Type a message and press Enter (or click Send). Tokens stream in as
   they arrive from the real provider.
5. Click **Stop** while generating to cancel -- reaches the upstream
   provider connection, not just the browser (see `docs/DESIGN.md`,
   "Cancellation").
6. Switch the model dropdown before the next message to route that turn
   to a different provider, keeping the full conversation coherent.
7. Refresh and reselect the conversation -- same history, including
   whether any reply was cut short (marked "stopped").

## Using RAG (documents)

1. In the **Documents (RAG)** panel, click **Create** to make a new
   collection (or pick an existing one from the dropdown).
2. Set **chunk size**/**overlap** if you want something other than the
   defaults (1000 characters / 150 overlap), then choose a `.txt`, `.md`,
   or `.pdf` file and click **Upload**. The document list shows its
   status: `processing` -> `ready` or `failed` (with a reason -- a
   scanned PDF with no text layer fails honestly here, it is not OCR'd).
3. With a collection selected, send a chat message as usual. The reply is
   answered only from that collection's evidence, streamed exactly like
   ordinary chat, with a **Sources** list underneath -- click a source
   chip (`[S1]`, `[S2]`, ...) to see its filename, chunk index, page (PDF
   only), similarity score, and the actual retrieved text.
4. **Top-K**/**similarity threshold** (shown once a collection is
   selected) control retrieval per-query, independent of the chunk
   size/overlap used at upload time -- re-querying with different values
   never requires re-uploading anything.
5. If no chunk in the collection meets the similarity threshold, the
   reply is a deterministic "I don't know based on the provided
   documents." -- the model is never called in that case (see
   `docs/DESIGN.md`, "No-Evidence Behavior").
6. Select "No collection (plain chat)" to go back to ordinary,
   non-grounded chat at any time.

## RAG / embeddings

One embedding model is configured: OpenAI's `text-embedding-3-small`
(1536 dimensions, $0.02/MTok -- see `docs/PROVIDER_NOTES.md`'s
"Embeddings" section for full sourcing). Chosen because the OpenAI SDK
was already installed and used for chat; embedding support is one
additional method on the existing adapter (`OpenAIAdapter.embed()`), not
a new provider or a second abstraction layer.

## RAG / vector storage

No pgvector -- checked directly against this project's native PostgreSQL
install (not available; see `docs/DESIGN.md`'s CP-01 section for why
Docker wasn't an option either). Embeddings are stored in a plain
`double precision[]` Postgres column; similarity (cosine) is computed in
Python over rows a tenant/collection-scoped SQL query (enforced by row-
level security, same as every other table) already restricted -- never
computed over, or filtered down from, another tenant's data. See
`docs/DESIGN.md`'s "RAG Tenant Isolation" section for the exact guarantee
and the adversarial test that proves it.

## Configured models

| Internal id | Provider | Needs |
| --- | --- | --- |
| `claude-sonnet` | Anthropic | `ANTHROPIC_API_KEY` |
| `gpt-5.6-terra` | OpenAI | `OPENAI_API_KEY` |
| `gemini-2.5-flash` | Gemini | `GEMINI_API_KEY` |
| `text-embedding-3-small` | OpenAI (embeddings) | `OPENAI_API_KEY` |

Real, sourced pricing and capabilities for each are in
`backend/app/providers/models.yaml`; full API differences and sourcing in
`docs/PROVIDER_NOTES.md`.

## Running tests

Requires the database from the steps above to be running (tests exercise
real row-level-security behavior, not mocks). No provider API key is
required -- all provider/adapter/embedding tests use fixtures, never a
live call.

```bash
cd backend
./.venv/Scripts/python.exe -m pytest -v
```

## Tenant identifier (take-home simplification)

Requests are scoped by an `X-Tenant-Id` header (e.g. `tenant-a` or
`tenant-b`, both seeded automatically in development). This is
intentionally simple and unsigned per the assignment's own guidance for a
take-home -- see `docs/DESIGN.md` for what is and isn't protected by this,
and what production authentication would replace it with.

## Limitations (stated plainly)

- **No OCR.** A scanned/image-only PDF fails extraction honestly
  (`status: "failed"`, a clear reason) rather than silently producing no
  chunks or pretending text was recovered.
- **Prompt-injection defense is real but not a guarantee.** Retrieved
  document content is delimited and framed as untrusted data with an
  explicit instruction not to follow embedded commands -- tested that
  this framing is actually present in what's sent, not that a model will
  necessarily obey it. See `docs/DESIGN.md`, "Prompt Injection".
- **The similarity threshold default (0.3) is a reasonable take-home
  starting point, not a calibrated value** -- there's no evaluation
  dataset behind it. See `docs/DESIGN.md`, "Retrieval".
- **Chunking is character-based, not token-based**, and context-window
  trimming's token counts are estimates (~4 chars/token) -- both stated
  as approximations, not exact.

## Live vs. fixture-tested providers

**Anthropic** has been manually verified against the real API (CP-04): a
real streamed reply, a real mid-generation Stop, a real auth failure.
Gemini and OpenAI *chat* adapters are fixture-tested only. See
`docs/PROVIDER_NOTES.md`'s "Live- vs fixture-tested" note, and its
"Embeddings" section for the OpenAI embeddings verification status.

## What is done, partial, and cut so far

**Done:** everything from CP-01 through CP-04 (tenant-isolated
persistence, real SSE streaming, cancellation, provider switching), plus:
document upload (PDF/TXT/Markdown) with validation and a configurable
size limit; deterministic, configurable chunking; an embedding
abstraction reusing the CP-02 `Provider` interface; tenant/collection-
scoped vector retrieval with configurable top-k and similarity threshold;
server-side citation mapping (the model cannot fabricate trusted source
metadata); a deterministic "I don't know" path when no evidence is
found, without calling the model; a minimal RAG UI (collection
management, upload, retrieval settings, inspectable citations).

**Not started (by design -- later checkpoints):** tool execution,
retries/fallback, usage/cost dashboards, reranking, hybrid search,
semantic caching, an evaluation framework.

## Repository layout

```
polyglot/
├── frontend/
│   └── src/
│       ├── api/           client, conversations, models, collections, SSE parser
│       └── components/    Chat, MessageList, ModelSelector, ConversationList, CollectionPanel
├── backend/
│   ├── app/
│   │   ├── api/            HTTP routes (health, models, conversations, collections)
│   │   ├── core/            settings, tenant context
│   │   ├── db/               pool, migrations, seed data
│   │   ├── providers/     provider-neutral contracts, registries, adapters
│   │   ├── repositories/  tenant-scoped data access (incl. collections/documents/chunks)
│   │   ├── schemas/         request/response models
│   │   └── services/         chat orchestration, context-window trimming,
│   │                            extraction, chunking, embeddings, retrieval, RAG grounding
│   └── tests/
│       └── rag_fixtures/  hand-built minimal PDFs used by extraction/ingestion tests
├── docs/
│   ├── DESIGN.md
│   ├── PROVIDER_NOTES.md
│   └── AI_USAGE.md
├── .env.example
└── README.md
```
