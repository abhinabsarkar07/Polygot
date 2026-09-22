"""A deterministic fake Provider.embed() for RAG tests -- no paid API
calls (STEP 28). ``vectors`` lets a test pin exact vectors for exact
input strings (needed for precise top-k/threshold assertions); any text
not explicitly pinned falls back to a deterministic hash-based vector, so
two calls with the same text always produce the same vector without a
test having to pin every single one.
"""

import hashlib
import random
from collections.abc import AsyncIterator

from app.providers.base import Provider
from app.providers.contracts import CompletionRequest, CompletionResponse, StreamEvent


class FakeEmbeddingProvider(Provider):
    id = "fake-embed"

    def __init__(self, dimension: int = 8, vectors: dict[str, list[float]] | None = None) -> None:
        self.dimension = dimension
        self._vectors = vectors or {}
        self.embed_call_count = 0

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise NotImplementedError

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        raise NotImplementedError
        yield  # pragma: no cover -- makes this an async generator function

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        self.embed_call_count += 1
        return [self._vector_for(t) for t in texts]

    def _vector_for(self, text: str) -> list[float]:
        if text in self._vectors:
            return self._vectors[text]
        # SHA-256 alone is only 32 bytes -- nowhere near enough for a
        # 1536-dimension vector (the real chunks table's own CHECK
        # constraint requires exactly that many, see 007_chunks.sql). The
        # digest just seeds a PRNG so an arbitrary dimension is still
        # produced deterministically from the same text.
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest(), "big")
        rng = random.Random(seed)
        return [rng.uniform(-1.0, 1.0) for _ in range(self.dimension)]
