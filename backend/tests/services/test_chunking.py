import pytest

from app.services.chunking import MAX_CHUNK_SIZE, ChunkingConfig, chunk_text


def test_normal_chunking_produces_overlapping_windows():
    text = "a" * 250
    config = ChunkingConfig(chunk_size=100, overlap=20)
    chunks = chunk_text(text, config)

    assert len(chunks) == 4  # steps of 80: 0-100, 80-180, 160-250(90), 240-250... let's just assert overlap held
    assert chunks[0][-20:] == chunks[1][:20]  # the actual overlap
    assert "".join(c for c in chunks[0]) == text[0:100]


def test_overlap_zero_produces_contiguous_non_overlapping_chunks():
    text = "x" * 300
    chunks = chunk_text(text, ChunkingConfig(chunk_size=100, overlap=0))
    assert chunks == [text[0:100], text[100:200], text[200:300]]


def test_chunk_size_below_minimum_rejected():
    with pytest.raises(ValueError, match="chunk_size"):
        ChunkingConfig(chunk_size=10, overlap=0)


def test_chunk_size_above_maximum_rejected():
    with pytest.raises(ValueError, match="chunk_size"):
        ChunkingConfig(chunk_size=MAX_CHUNK_SIZE + 1, overlap=0)


def test_negative_overlap_rejected():
    with pytest.raises(ValueError, match="overlap"):
        ChunkingConfig(chunk_size=500, overlap=-1)


def test_overlap_equal_to_chunk_size_rejected():
    with pytest.raises(ValueError, match="overlap"):
        ChunkingConfig(chunk_size=500, overlap=500)


def test_overlap_greater_than_chunk_size_rejected():
    with pytest.raises(ValueError, match="overlap"):
        ChunkingConfig(chunk_size=500, overlap=600)


def test_empty_text_produces_no_chunks():
    assert chunk_text("", ChunkingConfig(chunk_size=500, overlap=50)) == []
    assert chunk_text("   \n\t  ", ChunkingConfig(chunk_size=500, overlap=50)) == []


def test_short_text_produces_one_chunk():
    chunks = chunk_text("hello world", ChunkingConfig(chunk_size=500, overlap=50))
    assert chunks == ["hello world"]


def test_chunking_is_deterministic():
    text = "The quick brown fox jumps over the lazy dog. " * 20
    config = ChunkingConfig(chunk_size=150, overlap=30)
    assert chunk_text(text, config) == chunk_text(text, config)
