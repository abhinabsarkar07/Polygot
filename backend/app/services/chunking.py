"""Deterministic, character-based chunking.

Character-based, not token-based, and said so explicitly -- like
``app/services/context_window.py``, this project has no free, universal
tokenizer that works identically across all three configured chat
providers, and adding one just to chunk documents more "precisely" would
be a real dependency for a benefit this take-home doesn't need. Chunk
boundaries don't need to be exact token counts; they need to be
deterministic, configurable, and reasonable.
"""

from dataclasses import dataclass

MIN_CHUNK_SIZE = 100
MAX_CHUNK_SIZE = 8_000


@dataclass(frozen=True)
class ChunkingConfig:
    chunk_size: int
    overlap: int

    def __post_init__(self) -> None:
        if not (MIN_CHUNK_SIZE <= self.chunk_size <= MAX_CHUNK_SIZE):
            raise ValueError(f"chunk_size must be between {MIN_CHUNK_SIZE} and {MAX_CHUNK_SIZE} characters")
        if self.overlap < 0:
            raise ValueError("overlap must be >= 0")
        if self.overlap >= self.chunk_size:
            raise ValueError("overlap must be less than chunk_size")


def chunk_text(text: str, config: ChunkingConfig) -> list[str]:
    """Slides a ``chunk_size``-character window over ``text``, advancing
    by ``chunk_size - overlap`` each step. Whitespace-only slices are
    dropped (common at the very end of a document) rather than stored as
    empty evidence."""
    stripped = text.strip()
    if not stripped:
        return []

    step = config.chunk_size - config.overlap
    chunks: list[str] = []
    start = 0
    while start < len(stripped):
        piece = stripped[start : start + config.chunk_size]
        if piece.strip():
            chunks.append(piece)
        start += step
    return chunks
