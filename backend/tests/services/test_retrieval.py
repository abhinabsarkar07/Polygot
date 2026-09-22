"""Retrieval: top-k, threshold, ordering, source metadata -- and the most
important test in this checkpoint, cross-tenant chunk isolation.
Vectors are hand-picked (not the fake provider's hash-based ones) so
exact cosine similarities -- and therefore threshold/top-k behavior --
are known and asserted precisely, not just "roughly ranked".
"""

import pytest

from app.db.pool import tenant_connection
from app.repositories.chunks import ChunkRepository
from app.repositories.collections import CollectionRepository
from app.repositories.documents import DocumentRepository
from app.providers.registry import ProviderRegistry
from app.services.embeddings import EmbeddingService
from app.services.retrieval import RetrievalService, cosine_similarity
from tests.services.fake_embedding_provider import FakeEmbeddingProvider
from tests.services.test_embeddings import _embedding_model_config

# The chunks table's own CHECK constraint (007_chunks.sql) enforces the
# real 1536-dimension embedding size (text-embedding-3-small) -- so test
# vectors are padded to that length with trailing zeros, which changes
# neither their dot product nor their norm, and therefore not their
# cosine similarity either. Only the first two "meaningful" dimensions
# actually vary between these fixtures.
DIM = 1536


def _pad(*components: float) -> list[float]:
    return list(components) + [0.0] * (DIM - len(components))


QUERY_VECTOR = _pad(1.0, 0.0)
IDENTICAL = _pad(1.0, 0.0)  # cos = 1.0
RELATED = _pad(0.5, 0.8660254)  # cos = 0.5
UNRELATED = _pad(0.0, 1.0)  # cos = 0.0
OPPOSITE = _pad(-1.0, 0.0)  # cos = -1.0


def _retrieval_service(query_text: str = "the query") -> tuple[RetrievalService, FakeEmbeddingProvider]:
    provider = FakeEmbeddingProvider(dimension=DIM, vectors={query_text: QUERY_VECTOR})
    registry = ProviderRegistry()
    registry.register(provider)
    embeddings = EmbeddingService(registry, _embedding_model_config(dimension=DIM))
    return RetrievalService(embeddings), provider


async def _seed(pool, tenant, *, collection_name: str, chunks: list[tuple[str, list[float]]], filename: str = "doc.txt"):
    async with tenant_connection(pool, tenant) as conn:
        collection = await CollectionRepository(conn, tenant).create(collection_name)
        document = await DocumentRepository(conn, tenant).create(collection_id=collection.id, filename=filename, content_type="text/plain")
        await ChunkRepository(conn, tenant).create_many(
            collection_id=collection.id,
            document_id=document.id,
            texts=[text for text, _ in chunks],
            embeddings=[vec for _, vec in chunks],
            page_numbers=[None] * len(chunks),
        )
        await DocumentRepository(conn, tenant).mark_ready(document.id)
    return collection, document


async def test_cosine_similarity_known_values():
    assert cosine_similarity(QUERY_VECTOR, IDENTICAL) == pytest.approx(1.0)
    assert cosine_similarity(QUERY_VECTOR, RELATED) == pytest.approx(0.5, abs=1e-4)
    assert cosine_similarity(QUERY_VECTOR, UNRELATED) == pytest.approx(0.0, abs=1e-9)
    assert cosine_similarity(QUERY_VECTOR, OPPOSITE) == pytest.approx(-1.0)


async def test_threshold_excludes_unrelated_and_opposite_chunks(pool, tenant_a):
    service, _ = _retrieval_service()
    collection, _ = await _seed(
        pool, tenant_a, collection_name="c1",
        chunks=[("identical", IDENTICAL), ("related", RELATED), ("unrelated", UNRELATED), ("opposite", OPPOSITE)],
    )
    async with tenant_connection(pool, tenant_a) as conn:
        results = await service.retrieve(
            chunk_repository=ChunkRepository(conn, tenant_a), collection_id=collection.id, query="the query",
            top_k=10, similarity_threshold=0.3,
        )
    assert [r.source.chunk.text for r in results] == ["identical", "related"]


async def test_results_are_ordered_most_similar_first(pool, tenant_a):
    service, _ = _retrieval_service()
    collection, _ = await _seed(
        pool, tenant_a, collection_name="c1",
        chunks=[("related", RELATED), ("identical", IDENTICAL), ("unrelated", UNRELATED)],  # inserted out of similarity order
    )
    async with tenant_connection(pool, tenant_a) as conn:
        results = await service.retrieve(
            chunk_repository=ChunkRepository(conn, tenant_a), collection_id=collection.id, query="the query",
            top_k=10, similarity_threshold=-1.0,
        )
    assert [r.source.chunk.text for r in results] == ["identical", "related", "unrelated"]
    assert results[0].similarity > results[1].similarity > results[2].similarity


async def test_top_k_limits_result_count(pool, tenant_a):
    service, _ = _retrieval_service()
    collection, _ = await _seed(
        pool, tenant_a, collection_name="c1",
        chunks=[("identical", IDENTICAL), ("related", RELATED), ("unrelated", UNRELATED)],
    )
    async with tenant_connection(pool, tenant_a) as conn:
        results = await service.retrieve(
            chunk_repository=ChunkRepository(conn, tenant_a), collection_id=collection.id, query="the query",
            top_k=1, similarity_threshold=-1.0,
        )
    assert len(results) == 1
    assert results[0].source.chunk.text == "identical"


async def test_source_metadata_preserved(pool, tenant_a):
    service, _ = _retrieval_service()
    collection, document = await _seed(
        pool, tenant_a, collection_name="c1", chunks=[("identical", IDENTICAL)], filename="policy.txt"
    )
    async with tenant_connection(pool, tenant_a) as conn:
        [result] = await service.retrieve(
            chunk_repository=ChunkRepository(conn, tenant_a), collection_id=collection.id, query="the query",
            top_k=5, similarity_threshold=-1.0,
        )
    assert result.source.filename == "policy.txt"
    assert result.source.chunk.document_id == document.id
    assert result.source.chunk.chunk_index == 0
    assert result.source.chunk.text == "identical"


async def test_only_correct_collection_is_searched(pool, tenant_a):
    service, _ = _retrieval_service()
    collection_1, _ = await _seed(pool, tenant_a, collection_name="c1", chunks=[("in collection 1", IDENTICAL)])
    collection_2, _ = await _seed(pool, tenant_a, collection_name="c2", chunks=[("in collection 2", IDENTICAL)])

    async with tenant_connection(pool, tenant_a) as conn:
        results = await service.retrieve(
            chunk_repository=ChunkRepository(conn, tenant_a), collection_id=collection_1.id, query="the query",
            top_k=10, similarity_threshold=-1.0,
        )
    assert [r.source.chunk.text for r in results] == ["in collection 1"]


async def test_empty_collection_returns_no_results(pool, tenant_a):
    service, provider = _retrieval_service()
    async with tenant_connection(pool, tenant_a) as conn:
        collection = await CollectionRepository(conn, tenant_a).create("empty")

    async with tenant_connection(pool, tenant_a) as conn:
        results = await service.retrieve(
            chunk_repository=ChunkRepository(conn, tenant_a), collection_id=collection.id, query="the query",
            top_k=5, similarity_threshold=0.0,
        )
    assert results == []
    assert provider.embed_call_count == 0  # no chunks to compare against -- never even embeds the query


async def test_top_k_out_of_range_rejected(pool, tenant_a):
    service, provider = _retrieval_service()
    collection, _ = await _seed(pool, tenant_a, collection_name="c1", chunks=[("x", IDENTICAL)])

    async with tenant_connection(pool, tenant_a) as conn:
        with pytest.raises(ValueError, match="top_k"):
            await service.retrieve(
                chunk_repository=ChunkRepository(conn, tenant_a), collection_id=collection.id, query="the query",
                top_k=0, similarity_threshold=0.0,
            )
    # Rejected before any embedding call was made.
    assert provider.embed_call_count == 0


# --- The most important test in this checkpoint --------------------------------


async def test_tenant_a_can_never_retrieve_tenant_bs_chunks(pool, tenant_a, tenant_b):
    service, _ = _retrieval_service()

    # Tenant B has a collection with a chunk *identical* to what tenant A
    # will query for -- the highest possible similarity score, the case
    # most likely to leak if isolation were merely a WHERE clause someone
    # forgot, rather than RLS.
    collection_b, _ = await _seed(pool, tenant_b, collection_name="tenant-b-secret", chunks=[("tenant B's secret", IDENTICAL)])
    # Tenant A has their own, unrelated collection.
    collection_a, _ = await _seed(pool, tenant_a, collection_name="tenant-a-own", chunks=[("tenant A's own data", RELATED)])

    async with tenant_connection(pool, tenant_a) as conn:
        # Query tenant A's own collection -- must only ever see tenant A's chunk.
        results = await service.retrieve(
            chunk_repository=ChunkRepository(conn, tenant_a), collection_id=collection_a.id, query="the query",
            top_k=10, similarity_threshold=-1.0,
        )
        assert [r.source.chunk.text for r in results] == ["tenant A's own data"]

        # Attempting to query tenant B's collection ID *while scoped as
        # tenant A* must see nothing -- not tenant B's chunk, not an
        # error revealing the collection exists, just zero results (RLS
        # makes tenant B's collection invisible to this connection at all).
        results_for_bs_collection = await service.retrieve(
            chunk_repository=ChunkRepository(conn, tenant_a), collection_id=collection_b.id, query="the query",
            top_k=10, similarity_threshold=-1.0,
        )
        assert results_for_bs_collection == []

    # And direct collection access as tenant A must also fail for tenant B's id.
    async with tenant_connection(pool, tenant_a) as conn:
        found = await CollectionRepository(conn, tenant_a).get(collection_b.id)
        assert found is None
