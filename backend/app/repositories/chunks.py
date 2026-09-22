from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.repositories.base import TenantScopedRepository


@dataclass(frozen=True)
class Chunk:
    id: UUID
    tenant_id: UUID
    collection_id: UUID
    document_id: UUID
    chunk_index: int
    text: str
    embedding: list[float]
    page_number: int | None
    created_at: datetime


@dataclass(frozen=True)
class ChunkWithSource:
    """A chunk plus the one piece of its parent document retrieval/citation
    code actually needs -- avoids callers doing their own join."""

    chunk: Chunk
    filename: str


class ChunkRepository(TenantScopedRepository):
    async def create_many(
        self, *, collection_id: UUID, document_id: UUID, texts: list[str], embeddings: list[list[float]], page_numbers: list[int | None]
    ) -> None:
        assert len(texts) == len(embeddings) == len(page_numbers)
        # executemany -- one round trip, not N -- for what's otherwise a
        # tight per-chunk INSERT loop during ingestion.
        await self._conn.executemany(
            """
            INSERT INTO chunks (tenant_id, collection_id, document_id, chunk_index, text, embedding, page_number)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            [
                (self._tenant.id, collection_id, document_id, index, text, embedding, page_number)
                for index, (text, embedding, page_number) in enumerate(zip(texts, embeddings, page_numbers))
            ],
        )

    async def list_for_collection_with_filenames(self, collection_id: UUID) -> list[ChunkWithSource]:
        # Tenant AND collection scoping both happen in this one WHERE
        # clause (plus RLS, independently, on both tables) -- nothing
        # broader is ever fetched and filtered afterward. Similarity
        # ranking (not tenant/collection filtering) is what happens in
        # Python, in app/services/retrieval.py -- see 007_chunks.sql for
        # why (no pgvector on this install).
        # ORDER BY matters even though retrieval re-sorts by similarity
        # afterward: without it, Postgres makes no row-order guarantee at
        # all (confirmed the hard way -- this came back in a different
        # order once the table held more than one test's worth of data).
        rows = await self._conn.fetch(
            """
            SELECT c.id, c.tenant_id, c.collection_id, c.document_id, c.chunk_index, c.text,
                   c.embedding, c.page_number, c.created_at, d.filename
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.collection_id = $1
            ORDER BY c.document_id, c.chunk_index
            """,
            collection_id,
        )
        return [
            ChunkWithSource(
                chunk=Chunk(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    collection_id=row["collection_id"],
                    document_id=row["document_id"],
                    chunk_index=row["chunk_index"],
                    text=row["text"],
                    embedding=list(row["embedding"]),
                    page_number=row["page_number"],
                    created_at=row["created_at"],
                ),
                filename=row["filename"],
            )
            for row in rows
        ]
