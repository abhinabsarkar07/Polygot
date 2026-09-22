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
