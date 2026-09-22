from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.repositories.base import TenantScopedRepository


@dataclass(frozen=True)
class Document:
    id: UUID
    tenant_id: UUID
    collection_id: UUID
    filename: str
    content_type: str
    status: str
    error: str | None
    created_at: datetime


class DocumentRepository(TenantScopedRepository):
    async def create(self, *, collection_id: UUID, filename: str, content_type: str) -> Document:
        row = await self._conn.fetchrow(
            """
            INSERT INTO documents (tenant_id, collection_id, filename, content_type, status)
            VALUES ($1, $2, $3, $4, 'processing')
            RETURNING id, tenant_id, collection_id, filename, content_type, status, error, created_at
            """,
            self._tenant.id,
            collection_id,
            filename,
            content_type,
        )
        return Document(**row)

    async def mark_ready(self, document_id: UUID) -> None:
        await self._conn.execute("UPDATE documents SET status = 'ready' WHERE id = $1", document_id)

    async def mark_failed(self, document_id: UUID, error: str) -> None:
        await self._conn.execute("UPDATE documents SET status = 'failed', error = $2 WHERE id = $1", document_id, error)

    async def get(self, document_id: UUID) -> Document | None:
        row = await self._conn.fetchrow(
            "SELECT id, tenant_id, collection_id, filename, content_type, status, error, created_at "
            "FROM documents WHERE id = $1",
            document_id,
        )
        return Document(**row) if row else None

    async def list_for_collection(self, collection_id: UUID) -> list[Document]:
        rows = await self._conn.fetch(
            "SELECT id, tenant_id, collection_id, filename, content_type, status, error, created_at "
            "FROM documents WHERE collection_id = $1 ORDER BY created_at",
            collection_id,
        )
        return [Document(**row) for row in rows]
