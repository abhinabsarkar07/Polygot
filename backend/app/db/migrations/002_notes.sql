-- `notes` is a minimal tenant-owned resource that exists ONLY to prove the
-- tenant-scoped data-access pattern in CP-01 (repository + RLS). It is not
-- a real product feature -- conversations, documents, etc. arrive in later
-- checkpoints and will follow this exact same pattern.
CREATE TABLE notes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX notes_tenant_id_idx ON notes (tenant_id);

ALTER TABLE notes ENABLE ROW LEVEL SECURITY;

-- FORCE is what makes this structural rather than conventional: without
-- it, Postgres exempts the table's OWNER from its own RLS policies, and
-- the application connects as that owning role. FORCE applies the policy
-- to the owner too (superusers still bypass RLS regardless -- the app
-- role must never be a superuser).
ALTER TABLE notes FORCE ROW LEVEL SECURITY;

-- current_setting(..., true) returns NULL only for a GUC that has never
-- been touched on this session. Once a pooled connection has had
-- app.tenant_id set via SET LOCAL even once, Postgres does not revert it
-- to NULL when that transaction ends (or on RESET ALL, which asyncpg runs
-- when a connection is released back to the pool) -- it becomes ''
-- instead. Casting '' straight to uuid raises a Postgres error rather
-- than failing closed, so NULLIF(..., '') normalizes both "never touched"
-- and "touched, then reset" to NULL before the cast. Either way,
-- tenant_id = NULL is never true, so a connection with no *current*
-- tenant context set sees zero rows rather than every tenant's rows or
-- an error.
CREATE POLICY notes_tenant_isolation ON notes
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
