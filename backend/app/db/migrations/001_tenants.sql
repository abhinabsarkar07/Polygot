-- The tenant directory itself. This table is NOT tenant-owned data -- it's
-- the root list of tenants that every other tenant-owned table points at,
-- so it intentionally has no row-level security of its own. Looking a
-- tenant up by slug (app/core/tenant.py) is the one place the app reads
-- this table without a tenant context already established.
CREATE TABLE tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
