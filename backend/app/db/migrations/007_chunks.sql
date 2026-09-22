-- `embedding` is a plain Postgres `double precision[]` column, not
-- pgvector -- this machine's native PostgreSQL install has no pgvector
-- extension available (checked directly: absent from
-- pg_available_extensions, no vector.* files under the install's lib/ or
-- share/extension/), consistent with the CP-01 decision to defer it (see
-- docs/DESIGN.md's CP-01 "Persistence choice" section). Similarity is
-- computed in Python (app/services/retrieval.py) over vectors fetched
-- through this column -- but tenant AND collection filtering both happen
-- in this table's own WHERE clause and RLS policy, never by fetching
-- broadly and filtering in application memory afterward. `collection_id`
-- is denormalized here (also reachable via `documents`) specifically so
-- retrieval's tenant+collection scoping is a single-table WHERE clause,
-- not a join that has to be gotten right every time.
--
-- The dimension check (1536) matches `text-embedding-3-small`, the one
-- embedding model currently configured (see
-- app/providers/models.yaml) -- if a different embedding model with a
-- different dimension is ever configured, this constraint must change
-- with it, deliberately, not silently accept mismatched vectors.
CREATE TABLE chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    collection_id UUID NOT NULL REFERENCES collections(id),
    document_id UUID NOT NULL REFERENCES documents(id),
    chunk_index INT NOT NULL,
    text TEXT NOT NULL,
    embedding DOUBLE PRECISION[] NOT NULL,
    -- Only populated when extraction can actually attribute a chunk to a
    -- PDF page; never fabricated for TXT/Markdown or when unavailable.
    page_number INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chunks_embedding_dimension CHECK (array_length(embedding, 1) = 1536)
);

CREATE INDEX chunks_collection_id_idx ON chunks (collection_id);
CREATE INDEX chunks_document_id_idx ON chunks (document_id);
CREATE INDEX chunks_tenant_id_idx ON chunks (tenant_id);

ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks FORCE ROW LEVEL SECURITY;

CREATE POLICY chunks_tenant_isolation ON chunks
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
