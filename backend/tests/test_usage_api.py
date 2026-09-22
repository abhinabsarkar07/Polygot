"""API-level test for GET /api/usage/summary: shape of the response and,
mandatorily, that it never lets one tenant see another tenant's spend or
latency -- the same cross-tenant check every other endpoint gets.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers.contracts import DoneEvent, FinishReason, TextDeltaEvent, Usage, UsageEvent
from app.providers.models import ModelCapabilities, ModelConfig, ModelRegistry, PricingConfig
from app.providers.registry import ProviderRegistry
from app.services.chat import ChatService
from tests.services.fake_provider import ScriptedProvider

FAKE_MODEL_ID = "fake-model"
FAKE_PROVIDER_ID = "fake"


def _model_registry() -> ModelRegistry:
    config = ModelConfig(
        id=FAKE_MODEL_ID, provider=FAKE_PROVIDER_ID, provider_model_id="fake-v1", context_window=100_000,
        capabilities=ModelCapabilities(), pricing=PricingConfig(input_per_million=1.0, output_per_million=1.0),
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
        _install_fake_provider([])
        yield c


def _headers(tenant: str) -> dict:
    return {"X-Tenant-Id": tenant}


def _send_message(client: TestClient, tenant: str, conversation_id: str) -> None:
    resp = client.post(
        f"/api/conversations/{conversation_id}/messages/stream",
        json={"content": "hi", "model": FAKE_MODEL_ID},
        headers=_headers(tenant),
    )
    assert resp.status_code == 200
    list(resp.iter_lines())  # drain the SSE stream so the turn actually completes


def test_usage_summary_is_empty_before_any_turns(client):
    resp = client.get("/api/usage/summary", headers=_headers("tenant-a"))
    assert resp.status_code == 200
    assert resp.json() == {"providers": []}


def test_usage_summary_reflects_a_completed_turn(client, conversation_id_factory):
    _install_fake_provider([TextDeltaEvent(text="hello"), UsageEvent(usage=Usage(input_tokens=10, output_tokens=5)), DoneEvent(finish_reason=FinishReason.STOP)])
    conversation_id = conversation_id_factory("tenant-a")
    _send_message(client, "tenant-a", conversation_id)

    resp = client.get("/api/usage/summary", headers=_headers("tenant-a"))
    assert resp.status_code == 200
    providers = resp.json()["providers"]
    assert len(providers) == 1
    assert providers[0]["provider"] == FAKE_PROVIDER_ID
    assert providers[0]["request_count"] == 1


def test_usage_summary_never_leaks_across_tenants(client, conversation_id_factory):
    _install_fake_provider([TextDeltaEvent(text="hello"), UsageEvent(usage=Usage(input_tokens=10, output_tokens=5)), DoneEvent(finish_reason=FinishReason.STOP)])
    conversation_a = conversation_id_factory("tenant-a")
    _send_message(client, "tenant-a", conversation_a)

    resp_b = client.get("/api/usage/summary", headers=_headers("tenant-b"))
    assert resp_b.status_code == 200
    # Tenant B made zero requests -- if tenant A's usage row leaked across
    # the RLS boundary, this would show tenant A's provider/request instead.
    assert resp_b.json() == {"providers": []}

    resp_a = client.get("/api/usage/summary", headers=_headers("tenant-a"))
    assert resp_a.json()["providers"][0]["request_count"] == 1


@pytest.fixture
def conversation_id_factory(client):
    def _make(tenant: str) -> str:
        resp = client.post("/api/conversations", json={"title": "t"}, headers=_headers(tenant))
        assert resp.status_code == 200
        return resp.json()["id"]

    return _make
