"""Full-stack RAG: real upload through the API, real chat SSE stream with
a collection selected, and -- the most important test in this checkpoint
-- that Tenant A can never retrieve Tenant B's chunks even through the
complete upload-then-query path, not just at the repository layer.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.registry import ProviderRegistry
from app.services.chat import ChatService
from app.services.embeddings import EmbeddingService
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService
from tests.services.fake_embedding_provider import FakeEmbeddingProvider
from tests.services.fake_provider import ScriptedProvider
from tests.services.test_chat_service import _model_registry
from tests.services.test_embeddings import _embedding_model_config
from app.providers.contracts import DoneEvent, FinishReason, TextDeltaEvent

FAKE_MODEL_ID = "fake-model"
IDENTICAL = [1.0] + [0.0] * 1535


def _install_fake_rag(chat_events: list, query_text_vectors: dict[str, list[float]] | None = None) -> None:
    chat_registry = ProviderRegistry()
    chat_registry.register(ScriptedProvider("fake", chat_events))
    embed_registry = ProviderRegistry()
    embed_registry.register(FakeEmbeddingProvider(dimension=1536, vectors=query_text_vectors or {}))

    models = _model_registry(FAKE_MODEL_ID, "fake")
    app.state.model_registry = models
    embeddings = EmbeddingService(embed_registry, _embedding_model_config(dimension=1536))
    retrieval = RetrievalService(embeddings)
    app.state.ingestion_service = IngestionService(embeddings)
    app.state.chat_service = ChatService(models, chat_registry, retrieval=retrieval)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _headers(tenant: str) -> dict:
    return {"X-Tenant-Id": tenant}


def _parse_sse(body: str) -> list[tuple[str, str]]:
    events = []
    for block in body.strip("\n").split("\n\n"):
        if not block.strip():
            continue
        lines = block.splitlines()
        event_type = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        events.append((event_type, data))
    return events


def test_full_rag_flow_upload_then_grounded_chat_with_sources(client):
    _install_fake_rag(
        [TextDeltaEvent(text="30 days [S1]."), DoneEvent(finish_reason=FinishReason.STOP)],
        # Both the query AND the chunk text are pinned to the identical
        # vector -- guaranteed cos=1.0, well above the default threshold --
        # so this test proves the SSE plumbing carries real sources
        # through, not that the fake embedding happens to rank them well.
        query_text_vectors={
            "what is the refund window?": IDENTICAL,
            "Refunds are available within 30 days of purchase.": IDENTICAL,
        },
    )
    collection = client.post("/api/collections", json={"name": "policies"}, headers=_headers("tenant-a")).json()
    client.post(
        f"/api/collections/{collection['id']}/documents",
        headers=_headers("tenant-a"),
        files={"file": ("policy.txt", b"Refunds are available within 30 days of purchase.", "text/plain")},
    )
    conversation = client.post("/api/conversations", json={}, headers=_headers("tenant-a")).json()

    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "what is the refund window?", "model": FAKE_MODEL_ID, "collection_id": collection["id"]},
        headers=_headers("tenant-a"),
    )
    assert response.status_code == 200
    events = _parse_sse(response.text)
    assert events[0][0] == "sources"
    assert "policy.txt" in events[0][1]
    assert any(t == "text_delta" for t, _ in events)


def test_rag_chat_with_no_matching_evidence_answers_i_dont_know(client):
    _install_fake_rag([], query_text_vectors={"unrelated question": IDENTICAL})
    collection = client.post("/api/collections", json={"name": "policies"}, headers=_headers("tenant-a")).json()
    client.post(
        f"/api/collections/{collection['id']}/documents",
        headers=_headers("tenant-a"),
        files={"file": ("policy.txt", b"Completely unrelated content about gardening.", "text/plain")},
    )
    conversation = client.post("/api/conversations", json={}, headers=_headers("tenant-a")).json()

    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "unrelated question", "model": FAKE_MODEL_ID, "collection_id": collection["id"], "similarity_threshold": 0.9},
        headers=_headers("tenant-a"),
    )
    events = _parse_sse(response.text)
    assert events[0] == ("sources", '{"type":"sources","sources":[]}')
    assert any("I don't know based on the provided documents." in data for _, data in events)


# --- The most important test in this checkpoint --------------------------------


def test_tenant_a_never_sees_tenant_bs_documents_through_the_full_upload_and_chat_flow(client):
    _install_fake_rag(
        [TextDeltaEvent(text="answer"), DoneEvent(finish_reason=FinishReason.STOP)],
        query_text_vectors={"tell me about the secret": IDENTICAL},
    )

    # Tenant B uploads a document with content identical in embedding
    # space to what Tenant A will query for -- the case most likely to
    # leak if isolation depended on similarity/relevance rather than RLS.
    collection_b = client.post("/api/collections", json={"name": "tenant-b-collection"}, headers=_headers("tenant-b")).json()
    client.post(
        f"/api/collections/{collection_b['id']}/documents",
        headers=_headers("tenant-b"),
        files={"file": ("secret.txt", b"Tenant B's confidential secret document content.", "text/plain")},
    )

    # Tenant A has their own, separate collection and document.
    collection_a = client.post("/api/collections", json={"name": "tenant-a-collection"}, headers=_headers("tenant-a")).json()
    client.post(
        f"/api/collections/{collection_a['id']}/documents",
        headers=_headers("tenant-a"),
        files={"file": ("own.txt", b"Tenant A's own document content.", "text/plain")},
    )

    conversation = client.post("/api/conversations", json={}, headers=_headers("tenant-a")).json()

    # Querying tenant A's own collection must only ever surface tenant A's own content.
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages/stream",
        json={"content": "tell me about the secret", "model": FAKE_MODEL_ID, "collection_id": collection_a["id"], "similarity_threshold": -1.0},
        headers=_headers("tenant-a"),
    )
    events = _parse_sse(response.text)
    sources_data = events[0][1]
    assert "own.txt" in sources_data
    assert "secret.txt" not in sources_data
    assert "Tenant B" not in sources_data

    # Direct access to tenant B's collection ID while scoped as tenant A: 404.
    direct = client.get(f"/api/collections/{collection_b['id']}", headers=_headers("tenant-a"))
    assert direct.status_code == 404

    # And attempting to chat against tenant B's collection ID while scoped as tenant A also fails closed.
    conversation_2 = client.post("/api/conversations", json={}, headers=_headers("tenant-a")).json()
    cross_tenant_attempt = client.post(
        f"/api/conversations/{conversation_2['id']}/messages/stream",
        json={"content": "tell me about the secret", "model": FAKE_MODEL_ID, "collection_id": collection_b["id"], "similarity_threshold": -1.0},
        headers=_headers("tenant-a"),
    )
    # RLS makes tenant B's collection's chunks simply invisible to this
    # connection -- retrieval returns zero results (not an error, not
    # tenant B's content), which is exactly the deterministic
    # "no evidence" path.
    cross_events = _parse_sse(cross_tenant_attempt.text)
    assert cross_events[0] == ("sources", '{"type":"sources","sources":[]}')
    assert "secret.txt" not in cross_tenant_attempt.text
    assert "Tenant B" not in cross_tenant_attempt.text
