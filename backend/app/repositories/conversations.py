"""Tenant-scoped conversation storage. Same structural pattern as
NoteRepository (CP-01): no application-level ``WHERE tenant_id = ...`` --
row-level security on `conversations` does that filtering, so ``get()``
returning ``None`` for another tenant's id is a property of the database,
not of this code remembering to check.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.repositories.base import TenantScopedRepository


@dataclass(frozen=True)
class Conversation:
    id: UUID
    tenant_id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationRepository(TenantScopedRepository):
    async def create(self, title: str | None = None) -> Conversation:
        row = await self._conn.fetchrow(
            """
            INSERT INTO conversations (tenant_id, title)
            VALUES ($1, $2)
            RETURNING id, tenant_id, title, created_at, updated_at
            """,
            self._tenant.id,
            title,
        )
        return Conversation(**row)

    async def get(self, conversation_id: UUID) -> Conversation | None:
        row = await self._conn.fetchrow(
            "SELECT id, tenant_id, title, created_at, updated_at FROM conversations WHERE id = $1",
            conversation_id,
        )
        return Conversation(**row) if row else None

    async def list(self) -> list[Conversation]:
        rows = await self._conn.fetch(
            "SELECT id, tenant_id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
        )
        return [Conversation(**row) for row in rows]

    async def touch(self, conversation_id: UUID) -> None:
        await self._conn.execute("UPDATE conversations SET updated_at = now() WHERE id = $1", conversation_id)
