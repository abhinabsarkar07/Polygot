"""Base for repositories that operate within one tenant's data.

A subclass receives a connection that already has row-level security bound
to ``tenant`` (see ``app.db.pool.tenant_connection``) plus the tenant
context itself. Tenant identity is bound once, at construction -- callers
write ``repository.get(id)``, never ``repository.get(id, tenant_id=...)``,
so there is no per-call argument to forget or get wrong.
"""

import asyncpg

from app.core.tenant import TenantContext


class TenantScopedRepository:
    def __init__(self, conn: asyncpg.Connection, tenant: TenantContext) -> None:
        self._conn = conn
        self._tenant = tenant
