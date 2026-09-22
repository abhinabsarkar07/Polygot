"""Database pool and the tenant-scoped connection boundary.

``tenant_connection`` is the only sanctioned way application code gets a
database connection to read or write tenant-owned data. It does two
things every time: opens a transaction, and sets the Postgres session
variable ``app.tenant_id`` that every tenant-owned table's row-level
security policy checks against.

Why this is structural rather than conventional: RLS policies are defined
with ``FORCE ROW LEVEL SECURITY`` on the table itself. That means even a
query with no WHERE clause at all -- ``SELECT * FROM notes`` -- can only
ever see rows belonging to whichever tenant this connection's session
variable is currently set to. A developer who forgets a tenant filter
does not leak data; they just get the correct, already-filtered result.
And if ``app.tenant_id`` is never set on a connection (e.g. some future
code path grabs a raw connection instead), or was set earlier and then
reset when the connection was released back to the pool, the policy
resolves it to NULL (see the ``NULLIF`` in the migration -- a *touched*
custom GUC reverts to ``''``, not NULL, so the policy normalizes that
itself), the ``tenant_id = NULL`` comparison is never true, and every
query returns zero rows -- fails closed, not open.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg

from app.core.tenant import TenantContext


async def create_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=10)


@asynccontextmanager
async def tenant_connection(pool: asyncpg.Pool, tenant: TenantContext) -> AsyncIterator[asyncpg.Connection]:
    async with pool.acquire() as conn:
        async with conn.transaction():
            # set_config(..., is_local=true) is the parameterized equivalent
            # of `SET LOCAL app.tenant_id = '<value>'`. We use it (rather
            # than string-formatting a SET statement) so the tenant id can
            # never be an injection vector.
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", str(tenant.id))
            yield conn
