"""Tenant/collection-scoped vector retrieval.

**Similarity semantics (STEP 16), stated once, deliberately:** this
service ranks by **cosine similarity**, not distance. Higher always means
more similar. ``similarity_threshold`` is a minimum -- a chunk is kept
only when ``similarity >= threshold`` -- so raising the threshold is
always "be stricter," never accidentally the opposite. Cosine similarity
for normalized text embeddings like ``text-embedding-3-small`` ranges
roughly -1 to 1 in theory; unrelated passages typically score near 0,
related ones meaningfully higher. There is no evaluation dataset behind
the default (0.3) -- it's a defensible starting point for a take-home,
documented as needing real calibration before production use (see
docs/DESIGN.md).

No LLM involved anywhere in this module -- retrieval is deterministic and
independently testable from generation.
"""

import math
from dataclasses import dataclass

from app.repositories.chunks import ChunkRepository, ChunkWithSource
from app.services.embeddings import EmbeddingService

DEFAULT_TOP_K = 5
DEFAULT_SIMILARITY_THRESHOLD = 0.3
MIN_TOP_K = 1
MAX_TOP_K = 20


@dataclass(frozen=True)
class RetrievedChunk:
    source: ChunkWithSource
    similarity: float


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class RetrievalService:
    def __init__(self, embeddings: EmbeddingService) -> None:
        self._embeddings = embeddings

    async def retrieve(
        self,
        *,
        chunk_repository: ChunkRepository,
        collection_id,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> list[RetrievedChunk]:
        if not (MIN_TOP_K <= top_k <= MAX_TOP_K):
            raise ValueError(f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}")

        # Tenant + collection scoping happens entirely inside this one
        # query (RLS + WHERE collection_id = ...) -- everything returned
        # here already belongs to the caller's tenant and the requested
        # collection. Nothing broader is ever fetched.
        candidates = await chunk_repository.list_for_collection_with_filenames(collection_id)
        if not candidates:
            return []

        query_vector = await self._embeddings.embed_query(query)

        scored = [
            RetrievedChunk(source=candidate, similarity=cosine_similarity(query_vector, candidate.chunk.embedding))
            for candidate in candidates
        ]
        scored.sort(key=lambda r: r.similarity, reverse=True)
        above_threshold = [r for r in scored if r.similarity >= similarity_threshold]
        return above_threshold[:top_k]
