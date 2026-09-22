"""Upload -> validate -> extract -> chunk -> embed -> store, synchronous
and in-request (STEP 14: no Celery/Redis for a one-day take-home,
documented as a production gap in docs/DESIGN.md).

Same two-phase-commit shape as ``app/services/chat.py``, for the same
reason: the document row is created and committed *first*, independently
of whether extraction/chunking/embedding later succeeds, so a failure
midway is a real, visible ``status = 'failed'`` row with a reason -- never
a silently-missing document, and never one left stuck at ``'processing'``
forever. No connection is held open during the embedding API call.
"""

import dataclasses

import asyncpg

from app.core.tenant import TenantContext
from app.db.pool import tenant_connection
from app.providers.errors import ProviderError
from app.providers.registry import ProviderNotFoundError
from app.repositories.chunks import ChunkRepository
from app.repositories.documents import Document, DocumentRepository
from app.services.chunking import ChunkingConfig, chunk_text
from app.services.embeddings import EmbeddingService
from app.services.extraction import ExtractionError, extract


class IngestionService:
    def __init__(self, embeddings: EmbeddingService) -> None:
        self._embeddings = embeddings

    async def ingest(
        self,
        *,
        pool: asyncpg.Pool,
        tenant: TenantContext,
        collection_id,
        filename: str,
        content_type: str,
        data: bytes,
        chunking: ChunkingConfig,
    ) -> Document:
        async with tenant_connection(pool, tenant) as conn:
            document = await DocumentRepository(conn, tenant).create(
                collection_id=collection_id, filename=filename, content_type=content_type
            )

        try:
            pages = extract(filename, data)
            texts: list[str] = []
            page_numbers: list[int | None] = []
            for page in pages:
                pieces = chunk_text(page.text, chunking)
                texts.extend(pieces)
                page_numbers.extend([page.page_number] * len(pieces))
            if not texts:
                raise ExtractionError("document produced no usable chunks after extraction")

            embeddings = await self._embeddings.embed_documents(texts)
        except ExtractionError as exc:
            return await self._fail(pool, tenant, document, str(exc))
        except ProviderError as exc:
            # Embedding call failed (rate limit, auth, etc.) -- the safe,
            # normalized message (never a raw provider body -- see
            # app/providers/errors.py::safe_message) is what gets stored
            # and shown, consistent with CP-04's error-surfacing rule.
            return await self._fail(pool, tenant, document, f"embedding failed: {exc.message}")
        except ProviderNotFoundError:
            # Distinct from ProviderError above -- this means no
            # embedding-capable provider is configured at all (e.g.
            # OPENAI_API_KEY unset), not that a configured one failed.
            # EmbeddingService resolves its provider lazily specifically so
            # this surfaces here, as one more honest `status = 'failed'`
            # document, rather than as an uncaught 500 (STEP 14: handle
            # partial failure deliberately).
            return await self._fail(pool, tenant, document, "no embedding provider is configured")

        async with tenant_connection(pool, tenant) as conn:
            await ChunkRepository(conn, tenant).create_many(
                collection_id=collection_id, document_id=document.id, texts=texts, embeddings=embeddings, page_numbers=page_numbers
            )
            await DocumentRepository(conn, tenant).mark_ready(document.id)

        return dataclasses.replace(document, status="ready")

    async def _fail(self, pool: asyncpg.Pool, tenant: TenantContext, document: Document, error: str) -> Document:
        async with tenant_connection(pool, tenant) as conn:
            await DocumentRepository(conn, tenant).mark_failed(document.id, error)
        return dataclasses.replace(document, status="failed", error=error)
