"""API-level tests: collection CRUD, cross-tenant isolation, and document
upload validation (STEP 26) -- unsupported type, empty file, oversized
file, bad/other-tenant collection id, extraction failure. Uses a fake
embedding provider -- no paid API calls.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.providers.registry import ProviderRegistry
from app.services.embeddings import EmbeddingService
from app.services.ingestion import IngestionService
from tests.services.fake_embedding_provider import FakeEmbeddingProvider
from tests.services.test_embeddings import _embedding_model_config

FIXTURES = Path(__file__).parent / "rag_fixtures"


def _install_fake_ingestion() -> None:
    registry = ProviderRegistry()
    registry.register(FakeEmbeddingProvider(dimension=1536))
    embeddings = EmbeddingService(registry, _embedding_model_config(dimension=1536))
    app.state.ingestion_service = IngestionService(embeddings)


@pytest.fixture
def client():
    with TestClient(app) as c:
        _install_fake_ingestion()
        yield c


def _headers(tenant: str) -> dict:
    return {"X-Tenant-Id": tenant}


# --- Collection CRUD -----------------------------------------------------------


def test_create_and_list_collection(client):
    created = client.post("/api/collections", json={"name": "Handbook"}, headers=_headers("tenant-a"))
    assert created.status_code == 200
    collection_id = created.json()["id"]

    listed = client.get("/api/collections", headers=_headers("tenant-a"))
    assert any(c["id"] == collection_id for c in listed.json())


def test_get_collection_returns_empty_document_list_initially(client):
    created = client.post("/api/collections", json={"name": "Handbook"}, headers=_headers("tenant-a"))
    detail = client.get(f"/api/collections/{created.json()['id']}", headers=_headers("tenant-a"))
    assert detail.status_code == 200
    assert detail.json()["documents"] == []


# --- Tenant isolation -------------------------------------------------------------


def test_tenant_b_cannot_get_tenant_as_collection(client):
    created = client.post("/api/collections", json={"name": "secret"}, headers=_headers("tenant-a"))
    response = client.get(f"/api/collections/{created.json()['id']}", headers=_headers("tenant-b"))
    assert response.status_code == 404


def test_tenant_b_cannot_list_tenant_as_collection(client):
    created = client.post("/api/collections", json={"name": "secret"}, headers=_headers("tenant-a"))
    listed = client.get("/api/collections", headers=_headers("tenant-b"))
    assert all(c["id"] != created.json()["id"] for c in listed.json())


def test_tenant_b_cannot_upload_into_tenant_as_collection(client):
    created = client.post("/api/collections", json={"name": "secret"}, headers=_headers("tenant-a"))
    response = client.post(
        f"/api/collections/{created.json()['id']}/documents",
        headers=_headers("tenant-b"),
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )
    assert response.status_code == 404


# --- Upload validation -------------------------------------------------------------


def test_upload_bad_collection_id_returns_404(client):
    response = client.post(
        "/api/collections/00000000-0000-0000-0000-000000000000/documents",
        headers=_headers("tenant-a"),
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )
    assert response.status_code == 404


def test_upload_valid_txt_succeeds(client):
    created = client.post("/api/collections", json={"name": "c"}, headers=_headers("tenant-a"))
    response = client.post(
        f"/api/collections/{created.json()['id']}/documents",
        headers=_headers("tenant-a"),
        files={"file": ("notes.txt", b"Some real document content here.", "text/plain")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["filename"] == "notes.txt"


def test_upload_valid_markdown_succeeds(client):
    created = client.post("/api/collections", json={"name": "c"}, headers=_headers("tenant-a"))
    response = client.post(
        f"/api/collections/{created.json()['id']}/documents",
        headers=_headers("tenant-a"),
        files={"file": ("README.md", b"# Title\n\nSome content.", "text/markdown")},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_upload_valid_pdf_succeeds(client):
    created = client.post("/api/collections", json={"name": "c"}, headers=_headers("tenant-a"))
    data = (FIXTURES / "sample.pdf").read_bytes()
    response = client.post(
        f"/api/collections/{created.json()['id']}/documents",
        headers=_headers("tenant-a"),
        files={"file": ("sample.pdf", data, "application/pdf")},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_upload_unsupported_extension_rejected(client):
    created = client.post("/api/collections", json={"name": "c"}, headers=_headers("tenant-a"))
    response = client.post(
        f"/api/collections/{created.json()['id']}/documents",
        headers=_headers("tenant-a"),
        files={"file": ("archive.zip", b"PK\x03\x04fake zip content", "application/zip")},
    )
    assert response.status_code == 400


def test_upload_empty_file_rejected(client):
    created = client.post("/api/collections", json={"name": "c"}, headers=_headers("tenant-a"))
    response = client.post(
        f"/api/collections/{created.json()['id']}/documents",
        headers=_headers("tenant-a"),
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert response.status_code == 400


def test_upload_oversized_file_rejected(client, monkeypatch):
    tiny_limit_settings = Settings(database_url="postgresql://unused/unused", max_upload_bytes=10)
    monkeypatch.setattr("app.api.collections.get_settings", lambda: tiny_limit_settings)
    created = client.post("/api/collections", json={"name": "c"}, headers=_headers("tenant-a"))
    response = client.post(
        f"/api/collections/{created.json()['id']}/documents",
        headers=_headers("tenant-a"),
        files={"file": ("notes.txt", b"this file is definitely more than ten bytes long", "text/plain")},
    )
    assert response.status_code == 413


def test_upload_scanned_pdf_reports_extraction_failure_not_500(client):
    created = client.post("/api/collections", json={"name": "c"}, headers=_headers("tenant-a"))
    data = (FIXTURES / "scanned_no_text.pdf").read_bytes()
    response = client.post(
        f"/api/collections/{created.json()['id']}/documents",
        headers=_headers("tenant-a"),
        files={"file": ("scanned.pdf", data, "application/pdf")},
    )
    # A clean 200 with status="failed" -- extraction failure is an
    # expected, handled outcome, not a server error (STEP 6/14).
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "failed"
    assert "no extractable text" in body["error"]


def test_upload_invalid_chunking_config_rejected(client):
    created = client.post("/api/collections", json={"name": "c"}, headers=_headers("tenant-a"))
    response = client.post(
        f"/api/collections/{created.json()['id']}/documents",
        headers=_headers("tenant-a"),
        files={"file": ("notes.txt", b"some content", "text/plain")},
        data={"chunk_size": "500", "overlap": "500"},  # overlap == chunk_size, invalid
    )
    assert response.status_code == 400
