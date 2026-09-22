-- `collections` groups uploaded documents for RAG (CP-05). Same
-- tenant-isolation shape as every prior tenant-owned table -- see
-- 002_notes.sql for the full rationale (FORCE'd RLS, NULLIF-normalized
-- policy); not repeated in every migration file after that one.
CREATE TABLE collections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX collections_tenant_id_idx ON collections (tenant_id);

ALTER TABLE collections ENABLE ROW LEVEL SECURITY;
ALTER TABLE collections FORCE ROW LEVEL SECURITY;

CREATE POLICY collections_tenant_isolation ON collections
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
