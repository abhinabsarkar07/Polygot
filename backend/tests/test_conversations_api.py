"""API-level tests: conversation CRUD, cross-tenant isolation, the SSE
streaming endpoint's wire format, and safe error surfacing. Uses a
scripted fake Provider registered in place of the real (keyless in test
env) ones -- no paid API calls.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.contracts import DoneEvent, ErrorEvent, FinishReason, TextDeltaEvent
from app.providers.errors import ProviderErrorKind
from app.providers.models import ModelCapabilities, ModelConfig, ModelRegistry, PricingConfig
from app.providers.registry import ProviderRegistry
from app.services.chat import ChatService
from tests.services.fake_provider import ScriptedProvider

FAKE_MODEL_ID = "fake-model"
FAKE_PROVIDER_ID = "fake"


def _model_registry() -> ModelRegistry:
    config = ModelConfig(
        id=FAKE_MODEL_ID,
        provider=FAKE_PROVIDER_ID,
        provider_model_id="fake-v1",
        context_window=100_000,
        capabilities=ModelCapabilities(streaming=True, tools=True),
        pricing=PricingConfig(input_per_million=1.0, output_per_million=1.0),
    )
    return ModelRegistry({FAKE_MODEL_ID: config})


def _install_fake_provider(events: list) -> None:
    registry = ProviderRegistry()
    registry.register(ScriptedProvider(FAKE_PROVIDER_ID, events))
    models = _model_registry()
    app.state.model_registry = models
    app.state.provider_registry = registry
    app.state.chat_service = ChatService(models, registry)


@pytest.fixture
def client():
    with TestClient(app) as c:
        _install_fake_provider([])  # default: tests that stream override this
        yield c


def _headers(tenant: str) -> dict:
    return {"X-Tenant-Id": tenant}


# --- Conversation CRUD ----------------------------------------------------------


def test_create_and_list_conversation(client):
    created = client.post("/api/conversations", json={"title": "My chat"}, headers=_headers("tenant-a"))
    assert created.status_code == 200
    conversation_id = created.json()["id"]

    listed = client.get("/api/conversations", headers=_headers("tenant-a"))
    assert listed.status_code == 200
    assert any(c["id"] == conversation_id for c in listed.json())


def test_get_conversation_returns_persisted_messages(client):
    created = client.post("/api/conversations", json={}, headers=_headers("tenant-a"))
    conversation_id = created.json()["id"]

    detail = client.get(f"/api/conversations/{conversation_id}", headers=_headers("tenant-a"))
    assert detail.status_code == 200
    assert detail.json()["messages"] == []


def test_request_body_cannot_supply_tenant_id(client):
    # tenant_id isn't a field on CreateConversationRequest at all -- FastAPI
    # silently drops it; the conversation still belongs to the *header's*
    # tenant, never whatever the body claimed.
    response = client.post(
        "/api/conversations", json={"title": "x", "tenant_id": "tenant-b"}, headers=_headers("tenant-a")
    )
    assert response.status_code == 200
    detail = client.get(f"/api/conversations/{response.json()['id']}", headers=_headers("tenant-a")).json()
    assert detail["id"] == response.json()["id"]  # visible to tenant-a, i.e. the header, not the body


# --- Tenant isolation -------------------------------------------------------------


def test_tenant_b_cannot_get_tenant_as_conversation(client):
    created = client.post("/api/conversations", json={}, headers=_headers("tenant-a"))
    conversation_id = created.json()["id"]

    response = client.get(f"/api/conversations/{conversation_id}", headers=_headers("tenant-b"))
    assert response.status_code == 404  # not-found, not 403 -- doesn't confirm the id exists at all


def test_tenant_b_cannot_list_tenant_as_conversation(client):
    created = client.post("/api/conversations", json={"title": "secret"}, headers=_headers("tenant-a"))
    conversation_id = created.json()["id"]

    listed = client.get("/api/conversations", headers=_headers("tenant-b"))
    assert all(c["id"] != conversation_id for c in listed.json())


def test_tenant_b_cannot_stream_into_tenant_as_conversation(client):
    created = client.post("/api/conversations", json={}, headers=_headers("tenant-a"))
    conversation_id = created.json()["id"]

    response = client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        json={"content": "hi", "model": FAKE_MODEL_ID},
        headers=_headers("tenant-b"),
    )
    assert response.status_code == 404


# --- Streaming SSE format --------------------------------------------------------


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


def test_stream_forwards_normalized_events_as_sse(client):
    _install_fake_provider(
        [TextDeltaEvent(text="Hel"), TextDeltaEvent(text="lo"), DoneEvent(finish_reason=FinishReason.STOP)]
    )
    created = client.post("/api/conversations", json={}, headers=_headers("tenant-a"))
    conversation_id = created.json()["id"]

    response = client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        json={"content": "hi", "model": FAKE_MODEL_ID},
        headers=_headers("tenant-a"),
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse(response.text)
    assert [e[0] for e in events] == ["text_delta", "text_delta", "done"]
    assert '"text":"Hel"' in events[0][1] or "Hel" in events[0][1]

    # The assistant reply was actually persisted server-side.
    detail = client.get(f"/api/conversations/{conversation_id}", headers=_headers("tenant-a")).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["content"][0]["text"] == "Hello"


def test_stream_error_event_carries_no_raw_provider_detail(client):
    _install_fake_provider([ErrorEvent(kind=ProviderErrorKind.RATE_LIMIT, message="Rate limited by provider")])
    created = client.post("/api/conversations", json={}, headers=_headers("tenant-a"))
    conversation_id = created.json()["id"]

    response = client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        json={"content": "hi", "model": FAKE_MODEL_ID},
        headers=_headers("tenant-a"),
    )
    events = _parse_sse(response.text)
    assert events[0][0] == "error"
    assert "rate_limit" in events[0][1]
    assert "Traceback" not in response.text and "Exception" not in response.text


def test_unknown_model_returns_400_not_a_stream(client):
    created = client.post("/api/conversations", json={}, headers=_headers("tenant-a"))
    conversation_id = created.json()["id"]

    response = client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        json={"content": "hi", "model": "does-not-exist"},
        headers=_headers("tenant-a"),
    )
    assert response.status_code == 400


def test_unavailable_provider_returns_503(client):
    # A model configured for a provider that was never registered (no key).
    unavailable_model = ModelConfig(
        id="orphan-model", provider="never-registered", provider_model_id="x", context_window=1000,
        capabilities=ModelCapabilities(), pricing=PricingConfig(input_per_million=1.0, output_per_million=1.0),
    )
    models = ModelRegistry({"orphan-model": unavailable_model})
    app.state.model_registry = models
    app.state.chat_service = ChatService(models, app.state.provider_registry)

    created = client.post("/api/conversations", json={}, headers=_headers("tenant-a"))
    conversation_id = created.json()["id"]

    response = client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        json={"content": "hi", "model": "orphan-model"},
        headers=_headers("tenant-a"),
    )
    assert response.status_code == 503


# --- Models API --------------------------------------------------------------------


def test_models_endpoint_excludes_pricing_and_provider_model_id(client):
    response = client.get("/api/models", headers=_headers("tenant-a"))
    assert response.status_code == 200
    body = response.json()
    assert body == [
        {"id": FAKE_MODEL_ID, "provider": FAKE_PROVIDER_ID, "context_window": 100_000, "capabilities": {"streaming": True, "tools": True, "vision": False, "embeddings": False, "reasoning": False}}
    ]
    assert "pricing" not in response.text
    assert "provider_model_id" not in response.text
