# Polyglot -- Multi-Provider AI Workbench

**Status: CP-04.** A working end-to-end chat workbench: create a
conversation, pick a model (Anthropic, Gemini, or OpenAI -- whichever
have API keys configured), send a message, watch the reply stream in
token by token, stop generation mid-stream, switch providers between
turns in the same conversation, and reload the page without losing
anything. All of it is tenant-isolated at the database level.

**Not yet built:** RAG, document upload, embeddings, tool execution,
retries/fallback, usage dashboards. See `docs/DESIGN.md` for what's
planned and not yet started.

## Prerequisites

- **Node.js** 20+ and npm (frontend)
- **Python** 3.11+ (backend). On Windows, if the bare `python`/`pip`
  commands resolve to the Microsoft Store stub instead of a real
  interpreter, use the `py` launcher instead (`py -m venv`, etc.), as the
  commands below do.
- **PostgreSQL** 16, running locally and reachable on `localhost:5432`.
  No Docker/pgvector is required yet -- see `docs/DESIGN.md` for why
  (short version: pgvector is deferred to the RAG checkpoint, in favor of
  a hand-rolled vector store).
- **At least one provider API key** (Anthropic, Gemini, or OpenAI) if you
  want to actually send a message and see a real reply. Without one, the
  app still runs fully -- conversations, the model list, and tenant
  isolation all work -- but sending a message to an unconfigured
  provider returns a clean `503`, not a crash.

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
simply unavailable (see "Configured models" below), not a startup error.

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
   `tenant-a`'s conversations or vice versa; see "Tenant identifier"
   below).
2. Click **+ New Conversation**.
3. Pick a model from the dropdown -- it's populated from whichever models
   are configured in `backend/app/providers/models.yaml` (see "Configured
   models"), regardless of whether that provider's API key is actually
   set.
4. Type a message and press Enter (or click Send). Tokens stream in as
   they arrive from the real provider -- there is no artificial delay or
   fake typewriter effect.
5. Click **Stop** while generating to cancel -- this propagates all the
   way to the upstream provider connection, not just to the browser's
   rendering (see `docs/DESIGN.md`, "Cancellation").
6. Switch the model dropdown before sending the next message to route
   that turn to a different provider entirely, while keeping the full
   prior conversation coherent (see `docs/DESIGN.md`, "Provider
   Switching").
7. Refreshing the page and reselecting the conversation shows the same
   history, including whether any reply was cut short (marked "stopped").

## Configured models

| Internal id | Provider | Needs |
| --- | --- | --- |
| `claude-sonnet` | Anthropic | `ANTHROPIC_API_KEY` |
| `gpt-5.6-terra` | OpenAI | `OPENAI_API_KEY` |
| `gemini-2.5-flash` | Gemini | `GEMINI_API_KEY` |

Real, sourced pricing and capabilities for each are in
`backend/app/providers/models.yaml`; full API differences and sourcing in
`docs/PROVIDER_NOTES.md`.

## Running tests

Requires the database from the steps above to be running (tests exercise
real row-level-security behavior, not mocks). No provider API key is
required -- all provider/adapter tests use fixtures, never a live call.

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

## Live vs. fixture-tested providers

As of this checkpoint, **Anthropic has been manually verified against the
real API** -- a real streamed reply, a real mid-generation Stop, and a
real auth failure all confirmed working end-to-end through the actual
running app. Gemini and OpenAI adapters are fixture-tested only (no key
was available to verify them live); see `docs/PROVIDER_NOTES.md`'s
"Live- vs fixture-tested" note for exactly what that means and what it
doesn't.

## What is done, partial, and cut so far

**Done:** conversation + message persistence, tenant-isolated at the
database level; real SSE token streaming from a live provider through to
the browser; cancellation that reaches the upstream provider connection,
not just the UI; provider/model switching mid-conversation with coherent
history; deliberate (if approximate) context-window trimming; safe,
provider-neutral error surfacing; a minimal but fully functional React
chat UI.

**Not started (by design -- later checkpoints):** RAG, document upload,
embeddings, vector search, citations, tool execution, retries/fallback,
usage/cost dashboards.

## Repository layout

```
polyglot/
├── frontend/
│   └── src/
│       ├── api/           client, conversations, models, SSE parser
│       └── components/    Chat, MessageList, ModelSelector, ConversationList
├── backend/
│   ├── app/
│   │   ├── api/            HTTP routes (health, models, conversations)
│   │   ├── core/            settings, tenant context
│   │   ├── db/               pool, migrations, seed data
│   │   ├── providers/     provider-neutral contracts, registries, adapters
│   │   ├── repositories/  tenant-scoped data access
│   │   ├── schemas/         request/response models
│   │   └── services/         chat orchestration, context-window trimming
│   └── tests/
├── docs/
│   ├── DESIGN.md
│   ├── PROVIDER_NOTES.md
│   └── AI_USAGE.md
├── .env.example
└── README.md
```
