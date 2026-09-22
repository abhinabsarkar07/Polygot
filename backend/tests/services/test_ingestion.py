"""IngestionService: the full upload -> extract -> chunk -> embed -> store
flow against a real database, with a fake (no paid API) embedding
provider. Partial-failure handling (STEP 14) is the main thing under
test: a document must never end up `ready` unless every step actually
succeeded.
"""

from pathlib import Path

from app.db.pool import tenant_connection
from app.providers.registry import ProviderRegistry
from app.repositories.chunks import ChunkRepository
from app.repositories.documents import DocumentRepository
from app.services.chunking import ChunkingConfig
from app.services.embeddings import EmbeddingService
from app.services.ingestion import IngestionService
from tests.services.fake_embedding_provider import FakeEmbeddingProvider
from tests.services.test_embeddings import _embedding_model_config

FIXTURES = Path(__file__).parent.parent / "rag_fixtures"


def _ingestion_service(dimension: int = 1536) -> tuple[IngestionService, FakeEmbeddingProvider]:
    provider = FakeEmbeddingProvider(dimension=dimension)
    registry = ProviderRegistry()
    registry.register(provider)
    embeddings = EmbeddingService(registry, _embedding_model_config(dimension=dimension))
    return IngestionService(embeddings), provider


async def _make_collection(pool, tenant):
    from app.repositories.collections import CollectionRepository

    async with tenant_connection(pool, tenant) as conn:
        return await CollectionRepository(conn, tenant).create("test collection")


async def test_ingest_txt_succeeds_and_produces_ready_document_with_chunks(pool, tenant_a):
    service, provider = _ingestion_service()
    collection = await _make_collection(pool, tenant_a)

    document = await service.ingest(
        pool=pool, tenant=tenant_a, collection_id=collection.id, filename="notes.txt",
        content_type="text/plain", data=b"A" * 250, chunking=ChunkingConfig(chunk_size=100, overlap=20),
    )

    assert document.status == "ready"
    assert document.error is None
    assert provider.embed_call_count == 1  # one batched embedding call for all chunks

    async with tenant_connection(pool, tenant_a) as conn:
        chunks = await ChunkRepository(conn, tenant_a).list_for_collection_with_filenames(collection.id)
    assert len(chunks) == 4
    assert all(c.filename == "notes.txt" for c in chunks)
    assert [c.chunk.chunk_index for c in chunks] == [0, 1, 2, 3]


async def test_ingest_pdf_preserves_page_numbers(pool, tenant_a):
    service, _ = _ingestion_service()
    collection = await _make_collection(pool, tenant_a)
    data = (FIXTURES / "sample.pdf").read_bytes()

    document = await service.ingest(
        pool=pool, tenant=tenant_a, collection_id=collection.id, filename="sample.pdf",
        content_type="application/pdf", data=data, chunking=ChunkingConfig(chunk_size=1000, overlap=100),
    )

    assert document.status == "ready"
    async with tenant_connection(pool, tenant_a) as conn:
        chunks = await ChunkRepository(conn, tenant_a).list_for_collection_with_filenames(collection.id)
    assert chunks[0].chunk.page_number == 1


async def test_ingest_scanned_pdf_marks_document_failed_not_ready(pool, tenant_a):
    service, provider = _ingestion_service()
    collection = await _make_collection(pool, tenant_a)
    data = (FIXTURES / "scanned_no_text.pdf").read_bytes()

    document = await service.ingest(
        pool=pool, tenant=tenant_a, collection_id=collection.id, filename="scanned.pdf",
        content_type="application/pdf", data=data, chunking=ChunkingConfig(chunk_size=1000, overlap=100),
    )

    assert document.status == "failed"
    assert document.error is not None
    assert "no extractable text" in document.error
    assert provider.embed_call_count == 0  # never even attempted -- extraction failed first

    async with tenant_connection(pool, tenant_a) as conn:
        chunks = await ChunkRepository(conn, tenant_a).list_for_collection_with_filenames(collection.id)
    assert chunks == []  # no partial chunks left behind


async def test_ingest_with_no_embedding_provider_configured_fails_cleanly_not_500(pool, tenant_a):
    # Distinct from a configured provider failing (ProviderError, tested
    # above): here nothing is registered at all -- e.g. OPENAI_API_KEY
    # unset -- which is ProviderNotFoundError, a different exception
    # hierarchy entirely. Must still end up a clean `failed` document, not
    # an uncaught exception reaching the route as a 500.
    registry = ProviderRegistry()  # nothing registered
    embeddings = EmbeddingService(registry, _embedding_model_config(dimension=1536))
    service = IngestionService(embeddings)
    collection = await _make_collection(pool, tenant_a)

    document = await service.ingest(
        pool=pool, tenant=tenant_a, collection_id=collection.id, filename="notes.txt",
        content_type="text/plain", data=b"some real content", chunking=ChunkingConfig(chunk_size=500, overlap=50),
    )

    assert document.status == "failed"
    assert "no embedding provider is configured" in document.error


async def test_ingest_persists_failed_status_visibly_not_silently(pool, tenant_a):
    # A failed document is a real, visible row -- not rolled back to
    # nonexistence (STEP 14: "do not leave a document marked successfully
    # indexed if embedding/storage failed halfway through" implies the
    # opposite failure mode -- silently vanishing -- is just as wrong).
    service, _ = _ingestion_service()
    collection = await _make_collection(pool, tenant_a)

    document = await service.ingest(
        pool=pool, tenant=tenant_a, collection_id=collection.id, filename="empty.txt",
        content_type="text/plain", data=b"   ", chunking=ChunkingConfig(chunk_size=500, overlap=50),
    )

    assert document.status == "failed"
    async with tenant_connection(pool, tenant_a) as conn:
        stored = await DocumentRepository(conn, tenant_a).get(document.id)
    assert stored is not None
    assert stored.status == "failed"
