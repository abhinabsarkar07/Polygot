-- One row per chat turn's provider call (CP-06), tenant-owned like every
-- other table -- FORCE'd RLS, own tenant_id, same pattern since 002_notes.sql.
-- Deliberately NOT storing prompt/response text here -- this is metrics,
-- not a transcript (the actual message content already lives in
-- `messages`); usage records must never become a second copy of
-- potentially sensitive conversation content.
CREATE TABLE usage_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    conversation_id UUID NOT NULL REFERENCES conversations(id),

    provider TEXT NOT NULL,
    -- The model actually requested vs. the one that actually answered --
    -- can differ when fallback occurs (see app/services/chat.py). Both
    -- are internal ids, never a provider's own model string.
    requested_model_id TEXT NOT NULL,
    final_model_id TEXT NOT NULL,

    ttft_ms DOUBLE PRECISION,
    total_latency_ms DOUBLE PRECISION NOT NULL,

    input_tokens INT,
    output_tokens INT,
    cached_input_tokens INT,
    reasoning_tokens INT,

    -- NUMERIC, not FLOAT8 -- money is exact-decimal in app/services/cost.py
    -- (Decimal) and stays exact-decimal in storage; NULL means "no usage
    -- was reported to price from," never a fabricated $0.00.
    cost_usd NUMERIC(12, 6),

    finish_reason TEXT,
    retry_count INT NOT NULL DEFAULT 0,
    fallback_used BOOLEAN NOT NULL DEFAULT false,

    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX usage_records_tenant_id_idx ON usage_records (tenant_id);
CREATE INDEX usage_records_conversation_id_idx ON usage_records (conversation_id);
CREATE INDEX usage_records_tenant_provider_idx ON usage_records (tenant_id, provider);

ALTER TABLE usage_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_records FORCE ROW LEVEL SECURITY;

CREATE POLICY usage_records_tenant_isolation ON usage_records
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
