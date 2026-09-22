"""`notes` exists only to exercise the tenant-scoped repository + RLS
pattern in CP-01 -- see app/db/migrations/002_notes.sql. Real domain
tables (conversations, documents, ...) will get their own repositories
following this exact shape in later checkpoints.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.repositories.base import TenantScopedRepository


@dataclass(frozen=True)
class Note:
    id: UUID
    tenant_id: UUID
    title: str
    body: str
    created_at: datetime


class NoteRepository(TenantScopedRepository):
    async def create(self, title: str, body: str = "") -> Note:
        row = await self._conn.fetchrow(
            """
            INSERT INTO notes (tenant_id, title, body)
            VALUES ($1, $2, $3)
            RETURNING id, tenant_id, title, body, created_at
            """,
            self._tenant.id,
            title,
            body,
        )
        return Note(**row)

    async def get(self, note_id: UUID) -> Note | None:
        # Deliberately no "AND tenant_id = ..." here. Row-level security on
        # `notes` (FORCE'd, non-superuser owner) already restricts every
        # query on this connection to the current tenant -- this method
        # could not leak another tenant's row even if this WHERE clause
        # were missing or wrong. That's the property CP-01's isolation
        # test verifies.
        row = await self._conn.fetchrow(
            "SELECT id, tenant_id, title, body, created_at FROM notes WHERE id = $1",
            note_id,
        )
        return Note(**row) if row else None

    async def list(self) -> list[Note]:
        rows = await self._conn.fetch(
            "SELECT id, tenant_id, title, body, created_at FROM notes ORDER BY created_at"
        )
        return [Note(**row) for row in rows]
