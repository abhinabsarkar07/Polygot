# Polyglot -- Multi-Provider AI Workbench

**Status: foundation only (CP-01).** There is no chat, no AI provider
integration, and no RAG yet -- this README describes exactly what exists
right now: a React/Vite frontend, a FastAPI backend, and a
PostgreSQL-backed, tenant-isolated data layer, wired together and tested
end to end.

## Prerequisites

- **Node.js** 20+ and npm (frontend)
- **Python** 3.11+ (backend). On Windows, if the bare `python`/`pip`
  commands resolve to the Microsoft Store stub instead of a real
  interpreter, use the `py` launcher instead (`py -m venv`, etc.), as the
  commands below do.
- **PostgreSQL** 16, running locally and reachable on `localhost:5432`.
  No Docker/pgvector is required for this checkpoint -- see
  `docs/DESIGN.md` for why (short version: pgvector is deferred to the
  RAG checkpoint, in favor of a hand-rolled vector store).

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

Leave `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `OPENAI_API_KEY` empty for
now -- nothing in this checkpoint reads them.

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

Open the printed URL (default `http://localhost:5173`). The page shows
"Backend: Connected" once it can reach the FastAPI health endpoint.

## Running tests

Requires the database from the steps above to be running (tests exercise
real row-level-security behavior, not mocks -- that boundary is the one
thing in this checkpoint worth proving against the real thing).

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

## What is done, partial, and cut so far

**Done:** frontend/backend scaffolding; centralized settings; CORS
restricted to the configured frontend origin; a numbered-SQL migration
runner; row-level-security-enforced tenant isolation with an automated
test suite proving cross-tenant reads, lists, and inserts all fail, and
that a connection with no tenant context set sees zero rows.

**Not started (by design -- later checkpoints):** any AI provider
integration, chat, streaming, RAG, tool calling, usage/cost tracking,
retries/fallback.

## Repository layout

```
polyglot/
├── frontend/         React + TypeScript + Vite
├── backend/
│   ├── app/
│   │   ├── api/          HTTP routes
│   │   ├── core/         settings, tenant context
│   │   ├── db/           pool, migrations, seed data
│   │   ├── repositories/ tenant-scoped data access
│   │   └── schemas/      request/response models
│   └── tests/
├── docs/
│   ├── DESIGN.md
│   ├── PROVIDER_NOTES.md
│   └── AI_USAGE.md
├── .env.example
└── README.md
```
