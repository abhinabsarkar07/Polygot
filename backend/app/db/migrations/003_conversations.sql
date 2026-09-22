-- `conversations` is the first real tenant-owned domain table (previous
-- tenant-owned tables were `notes`, a CP-01 scaffold). Follows the exact
-- same structural-isolation pattern established there: own tenant_id
-- column, FORCE'd RLS, NULLIF-normalized policy. See app/db/pool.py and
-- 002_notes.sql for why each piece is there.
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX conversations_tenant_id_idx ON conversations (tenant_id);

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;

CREATE POLICY conversations_tenant_isolation ON conversations
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
