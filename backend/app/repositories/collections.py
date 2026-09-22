from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.repositories.base import TenantScopedRepository


@dataclass(frozen=True)
class Collection:
    id: UUID
    tenant_id: UUID
    name: str
    created_at: datetime


class CollectionRepository(TenantScopedRepository):
    async def create(self, name: str) -> Collection:
        row = await self._conn.fetchrow(
            "INSERT INTO collections (tenant_id, name) VALUES ($1, $2) RETURNING id, tenant_id, name, created_at",
            self._tenant.id,
            name,
        )
        return Collection(**row)

    async def get(self, collection_id: UUID) -> Collection | None:
        # No "AND tenant_id = ..." -- RLS on `collections` already scopes
        # this, same structural guarantee as every prior repository.
        row = await self._conn.fetchrow(
            "SELECT id, tenant_id, name, created_at FROM collections WHERE id = $1", collection_id
        )
        return Collection(**row) if row else None

    async def list(self) -> list[Collection]:
        rows = await self._conn.fetch("SELECT id, tenant_id, name, created_at FROM collections ORDER BY created_at DESC")
        return [Collection(**row) for row in rows]
