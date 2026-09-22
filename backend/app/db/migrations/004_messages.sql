-- `messages` gets its own tenant_id + RLS policy rather than relying on a
-- join through `conversations` for isolation -- defense in depth. Even if
-- a future query joined on a forged/guessed conversation_id, this table's
-- own policy independently blocks cross-tenant rows.
--
-- `content` is JSONB holding a list of normalized ContentBlock dicts (the
-- exact shape app/providers/messages.py's ContentBlock union serializes
-- to), not plain text. CP-04 only ever writes single-TextBlock lists, but
-- storing the real domain shape now means a future tool-call message needs
-- no schema change -- it's already representable.
--
-- `status` distinguishes a normally completed assistant response from one
-- cut short by cancellation or a client disconnect (see
-- app/services/chat.py and docs/DESIGN.md, "Cancellation") -- a partial
-- response is persisted, but never silently indistinguishable from a
-- complete one.
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id),
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'tool')),
    content JSONB NOT NULL,
    -- Internal model id that generated this message. NULL for user
    -- messages; set for assistant messages so which model answered is
    -- visible for observability, without that ever controlling which
    -- provider a later turn in the same conversation uses (see
    -- docs/DESIGN.md, "Provider Switching").
    model_id TEXT,
    status TEXT NOT NULL DEFAULT 'complete' CHECK (status IN ('complete', 'interrupted')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX messages_conversation_id_created_at_idx ON messages (conversation_id, created_at);
CREATE INDEX messages_tenant_id_idx ON messages (tenant_id);

ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages FORCE ROW LEVEL SECURITY;

CREATE POLICY messages_tenant_isolation ON messages
    USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
    WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
