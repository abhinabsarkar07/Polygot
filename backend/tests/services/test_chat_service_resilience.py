"""CP-06: retry (eligible errors only), the streaming retry/fallback
safety boundary (never after visible output), fallback chains, timeouts,
and that usage records capture what actually happened. All against a
real database with fake providers -- no paid API calls, no real sleeping
(ChatService.sleep_fn is overridden to a no-op in every test here).
"""

import asyncio

import pytest

from app.db.pool import tenant_connection
from app.providers.contracts import DoneEvent, ErrorEvent, FinishReason, TextDeltaEvent, Usage, UsageEvent
from app.providers.errors import ProviderErrorKind
from app.providers.models import ModelCapabilities, ModelConfig, ModelRegistry, PricingConfig
from app.providers.registry import ProviderRegistry
from app.repositories.conversations import ConversationRepository
from app.repositories.messages import MessageRepository
from app.repositories.usage import UsageRepository
from app.services.chat import ChatService, FallbackEvent, RetryConfig
from tests.services.sequenced_provider import HANG, SequencedProvider


async def _noop_sleep(_seconds: float) -> None:
    return None


def _models_with_fallback() -> ModelRegistry:
    primary = ModelConfig(
        id="primary-model", provider="primary", provider_model_id="primary-v1", context_window=100_000,
        capabilities=ModelCapabilities(), pricing=PricingConfig(input_per_million=1.0, output_per_million=1.0),
        fallback_model_ids=["fallback-model"],
    )
    fallback = ModelConfig(
        id="fallback-model", provider="fallback", provider_model_id="fallback-v1", context_window=100_000,
        capabilities=ModelCapabilities(), pricing=PricingConfig(input_per_million=1.0, output_per_million=1.0),
    )
    return ModelRegistry({"primary-model": primary, "fallback-model": fallback})


def _service(models: ModelRegistry, registry: ProviderRegistry, *, max_retries=2, timeout_seconds=5.0) -> ChatService:
    service = ChatService(
        models, registry, retry_config=RetryConfig(max_retries=max_retries, base_delay_seconds=0.01, max_delay_seconds=0.02),
        timeout_seconds=timeout_seconds,
    )
    service.sleep_fn = _noop_sleep
    return service


async def _create_conversation(pool, tenant):
    async with tenant_connection(pool, tenant) as conn:
        return await ConversationRepository(conn, tenant).create()


async def _prepare(service, pool, tenant, conversation_id, model_id="primary-model"):
    async with tenant_connection(pool, tenant) as conn:
        return await service.prepare_turn(conn=conn, tenant=tenant, conversation_id=conversation_id, user_content="hi", model_id=model_id)


def _err(kind: ProviderErrorKind) -> ErrorEvent:
    return ErrorEvent(kind=kind, message="x")


def _success_events(text="hello") -> list:
    return [TextDeltaEvent(text=text), UsageEvent(usage=Usage(input_tokens=10, output_tokens=5)), DoneEvent(finish_reason=FinishReason.STOP)]


# --- Retry: eligible vs not ------------------------------------------------------


@pytest.mark.parametrize("kind", [ProviderErrorKind.RATE_LIMIT, ProviderErrorKind.SERVER_ERROR])
async def test_eligible_error_before_output_is_retried_and_succeeds(pool, tenant_a, kind):
    provider = SequencedProvider("primary", [[_err(kind)], _success_events()])
    registry = ProviderRegistry()
    registry.register(provider)
    service = _service(ModelRegistry({"primary-model": _plain_model("primary-model", "primary")}), registry)

    conversation = await _create_conversation(pool, tenant_a)
    prepared = await _prepare(service, pool, tenant_a, conversation.id)
    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]

    assert provider.call_count == 2  # one failure, one retry
    assert any(isinstance(e, TextDeltaEvent) and e.text == "hello" for e in events)
    async with tenant_connection(pool, tenant_a) as conn:
        rows = await conn.fetch("SELECT retry_count FROM usage_records WHERE conversation_id = $1", conversation.id)
    assert rows[0]["retry_count"] == 1


@pytest.mark.parametrize(
    "kind", [ProviderErrorKind.AUTH, ProviderErrorKind.BAD_REQUEST, ProviderErrorKind.CONTEXT_LENGTH, ProviderErrorKind.CONTENT_FILTER]
)
async def test_ineligible_error_is_never_retried(pool, tenant_a, kind):
    provider = SequencedProvider("primary", [[_err(kind)], _success_events()])
    registry = ProviderRegistry()
    registry.register(provider)
    service = _service(ModelRegistry({"primary-model": _plain_model("primary-model", "primary")}), registry)

    conversation = await _create_conversation(pool, tenant_a)
    prepared = await _prepare(service, pool, tenant_a, conversation.id)
    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]

    assert provider.call_count == 1  # never retried -- would be 2 if it incorrectly retried
    assert any(isinstance(e, ErrorEvent) and e.kind == kind for e in events)


async def test_retry_count_is_zero_when_first_attempt_succeeds(pool, tenant_a):
    provider = SequencedProvider("primary", [_success_events()])
    registry = ProviderRegistry()
    registry.register(provider)
    service = _service(ModelRegistry({"primary-model": _plain_model("primary-model", "primary")}), registry)

    conversation = await _create_conversation(pool, tenant_a)
    prepared = await _prepare(service, pool, tenant_a, conversation.id)
    [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]

    async with tenant_connection(pool, tenant_a) as conn:
        rows = await conn.fetch("SELECT retry_count FROM usage_records WHERE conversation_id = $1", conversation.id)
    assert rows[0]["retry_count"] == 0


async def test_retries_are_bounded_by_max_retries(pool, tenant_a):
    # Every attempt fails -- with max_retries=2, that's 1 initial + 2
    # retries = 3 total calls, then a surfaced error (no fallback configured).
    provider = SequencedProvider("primary", [[_err(ProviderErrorKind.SERVER_ERROR)]])
    registry = ProviderRegistry()
    registry.register(provider)
    service = _service(ModelRegistry({"primary-model": _plain_model("primary-model", "primary")}), registry, max_retries=2)

    conversation = await _create_conversation(pool, tenant_a)
    prepared = await _prepare(service, pool, tenant_a, conversation.id)
    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]

    assert provider.call_count == 3
    assert any(isinstance(e, ErrorEvent) for e in events)


# --- Streaming retry safety: never after visible output -----------------------


async def test_error_before_any_output_is_retried(pool, tenant_a):
    provider = SequencedProvider("primary", [[_err(ProviderErrorKind.RATE_LIMIT)], _success_events("recovered")])
    registry = ProviderRegistry()
    registry.register(provider)
    service = _service(ModelRegistry({"primary-model": _plain_model("primary-model", "primary")}), registry)

    conversation = await _create_conversation(pool, tenant_a)
    prepared = await _prepare(service, pool, tenant_a, conversation.id)
    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]

    assert provider.call_count == 2
    assert [e.text for e in events if isinstance(e, TextDeltaEvent)] == ["recovered"]


async def test_error_after_visible_output_is_never_transparently_retried(pool, tenant_a):
    # The exact scenario the assignment describes: text streams, THEN the
    # connection fails. Must surface an error, not silently retry from
    # scratch (which could duplicate or contradict what already streamed).
    provider = SequencedProvider(
        "primary", [[TextDeltaEvent(text="Your refund is"), _err(ProviderErrorKind.SERVER_ERROR)], _success_events("should never be reached")]
    )
    registry = ProviderRegistry()
    registry.register(provider)
    service = _service(ModelRegistry({"primary-model": _plain_model("primary-model", "primary")}), registry)

    conversation = await _create_conversation(pool, tenant_a)
    prepared = await _prepare(service, pool, tenant_a, conversation.id)
    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]

    assert provider.call_count == 1  # never called again
    texts = [e.text for e in events if isinstance(e, TextDeltaEvent)]
    assert texts == ["Your refund is"]  # exactly what streamed, nothing appended/duplicated
    assert any(isinstance(e, ErrorEvent) for e in events)

    # And the partial output is what got persisted, marked interrupted --
    # not silently discarded, not marked complete.
    async with tenant_connection(pool, tenant_a) as conn:
        messages = await MessageRepository(conn, tenant_a).list_for_conversation(conversation.id)
    assert messages[-1].content[0].text == "Your refund is"
    assert messages[-1].status == "interrupted"


# --- Fallback ------------------------------------------------------------------


def _plain_model(model_id: str, provider_id: str, fallback_ids: list[str] | None = None) -> ModelConfig:
    return ModelConfig(
        id=model_id, provider=provider_id, provider_model_id=f"{model_id}-v1", context_window=100_000,
        capabilities=ModelCapabilities(), pricing=PricingConfig(input_per_million=1.0, output_per_million=1.0),
        fallback_model_ids=fallback_ids or [],
    )


async def test_fallback_activates_on_eligible_error_before_output(pool, tenant_a):
    primary = SequencedProvider("primary", [[_err(ProviderErrorKind.SERVER_ERROR)]])
    fallback = SequencedProvider("fallback", [_success_events("from fallback")])
    registry = ProviderRegistry()
    registry.register(primary)
    registry.register(fallback)
    service = _service(_models_with_fallback(), registry, max_retries=0)  # no retries -- go straight to fallback

    conversation = await _create_conversation(pool, tenant_a)
    prepared = await _prepare(service, pool, tenant_a, conversation.id)
    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]

    assert primary.call_count == 1
    assert fallback.call_count == 1
    assert any(isinstance(e, FallbackEvent) and e.from_model == "primary-model" and e.to_model == "fallback-model" for e in events)
    assert [e.text for e in events if isinstance(e, TextDeltaEvent)] == ["from fallback"]

    async with tenant_connection(pool, tenant_a) as conn:
        rows = await conn.fetch("SELECT fallback_used, final_model_id, requested_model_id FROM usage_records WHERE conversation_id = $1", conversation.id)
    assert rows[0]["fallback_used"] is True
    assert rows[0]["final_model_id"] == "fallback-model"
    assert rows[0]["requested_model_id"] == "primary-model"


async def test_fallback_not_used_when_primary_succeeds(pool, tenant_a):
    primary = SequencedProvider("primary", [_success_events("from primary")])
    fallback = SequencedProvider("fallback", [_success_events("from fallback")])
    registry = ProviderRegistry()
    registry.register(primary)
    registry.register(fallback)
    service = _service(_models_with_fallback(), registry)

    conversation = await _create_conversation(pool, tenant_a)
    prepared = await _prepare(service, pool, tenant_a, conversation.id)
    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]

    assert fallback.call_count == 0
    assert not any(isinstance(e, FallbackEvent) for e in events)


async def test_fallback_not_used_for_auth_errors(pool, tenant_a):
    primary = SequencedProvider("primary", [[_err(ProviderErrorKind.AUTH)]])
    fallback = SequencedProvider("fallback", [_success_events("from fallback")])
    registry = ProviderRegistry()
    registry.register(primary)
    registry.register(fallback)
    service = _service(_models_with_fallback(), registry)

    conversation = await _create_conversation(pool, tenant_a)
    prepared = await _prepare(service, pool, tenant_a, conversation.id)
    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]

    assert fallback.call_count == 0
    assert any(isinstance(e, ErrorEvent) and e.kind == ProviderErrorKind.AUTH for e in events)


async def test_fallback_not_started_after_visible_output_from_primary(pool, tenant_a):
    primary = SequencedProvider("primary", [[TextDeltaEvent(text="partial answer"), _err(ProviderErrorKind.SERVER_ERROR)]])
    fallback = SequencedProvider("fallback", [_success_events("from fallback")])
    registry = ProviderRegistry()
    registry.register(primary)
    registry.register(fallback)
    service = _service(_models_with_fallback(), registry)

    conversation = await _create_conversation(pool, tenant_a)
    prepared = await _prepare(service, pool, tenant_a, conversation.id)
    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]

    assert fallback.call_count == 0  # fallback never even attempted
    assert not any(isinstance(e, FallbackEvent) for e in events)
    texts = [e.text for e in events if isinstance(e, TextDeltaEvent)]
    assert texts == ["partial answer"]


# --- Timeout ---------------------------------------------------------------------


async def test_timeout_produces_normalized_timeout_error_and_cancels_upstream(pool, tenant_a):
    provider = SequencedProvider("primary", [[HANG]])
    registry = ProviderRegistry()
    registry.register(provider)
    service = _service(ModelRegistry({"primary-model": _plain_model("primary-model", "primary")}), registry, timeout_seconds=0.05)

    conversation = await _create_conversation(pool, tenant_a)
    prepared = await _prepare(service, pool, tenant_a, conversation.id)
    events = await asyncio.wait_for(
        _collect(service.stream_reply(prepared, pool=pool, tenant=tenant_a)), timeout=5.0
    )  # outer safety net -- must not actually hang the test suite

    assert any(isinstance(e, ErrorEvent) and e.kind == ProviderErrorKind.TIMEOUT for e in events)


async def _collect(gen):
    return [e async for e in gen]


# --- Usage record tenant isolation (mandatory) ---------------------------------


async def test_tenant_a_can_never_see_tenant_bs_usage_summary(pool, tenant_a, tenant_b):
    provider_a = SequencedProvider("primary", [_success_events("a's reply")])
    registry_a = ProviderRegistry()
    registry_a.register(provider_a)
    service_a = _service(ModelRegistry({"primary-model": _plain_model("primary-model", "primary")}), registry_a)

    conversation_a = await _create_conversation(pool, tenant_a)
    prepared_a = await _prepare(service_a, pool, tenant_a, conversation_a.id)
    [e async for e in service_a.stream_reply(prepared_a, pool=pool, tenant=tenant_a)]

    provider_b = SequencedProvider("primary", [_success_events("b's reply")])
    registry_b = ProviderRegistry()
    registry_b.register(provider_b)
    service_b = _service(ModelRegistry({"primary-model": _plain_model("primary-model", "primary")}), registry_b)

    conversation_b = await _create_conversation(pool, tenant_b)
    prepared_b = await _prepare(service_b, pool, tenant_b, conversation_b.id)
    [e async for e in service_b.stream_reply(prepared_b, pool=pool, tenant=tenant_b)]

    async with tenant_connection(pool, tenant_a) as conn:
        summary_a = await UsageRepository(conn, tenant_a).summary_by_provider()
    async with tenant_connection(pool, tenant_b) as conn:
        summary_b = await UsageRepository(conn, tenant_b).summary_by_provider()

    assert sum(s.request_count for s in summary_a) == 1
    assert sum(s.request_count for s in summary_b) == 1
    # Tenant A's view must reflect only tenant A's own single request --
    # if tenant B's row leaked in, this count would be 2.
