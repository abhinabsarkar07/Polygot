-- `status` tracks synchronous ingestion outcome deliberately -- a
-- document is never visible as `ready` unless extraction, chunking, and
-- embedding all actually succeeded (see app/services/ingestion.py). A
-- failed upload stays visible (with `error` explaining why) rather than
-- silently vanishing or being left in a permanently ambiguous state.
CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    collection_id UUID NOT NULL REFERENCES collections(id),
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing' CHECK (status IN ('processing', 'ready', 'failed')),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX documents_collection_id_idx ON documents (collection_id);
CREATE INDEX documents_tenant_id_idx ON documents (tenant_id);

ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;

CREATE POLICY documents_tenant_isolation ON documents
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
