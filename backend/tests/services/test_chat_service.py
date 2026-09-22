"""ChatService: conversation/model/provider resolution, real persistence
against the database, and the exact cancellation/error persistence
behavior CP-04's DEFINITION OF DONE requires. Uses a scripted fake
Provider (tests/services/fake_provider.py) -- no paid API calls.
"""

import asyncio
from uuid import uuid4

import pytest

from app.db.pool import tenant_connection
from app.providers.contracts import DoneEvent, ErrorEvent, FinishReason, TextDeltaEvent, Usage, UsageEvent
from app.providers.errors import ProviderErrorKind
from app.providers.models import ModelCapabilities, ModelConfig, ModelNotFoundError, ModelRegistry, PricingConfig
from app.providers.registry import ProviderNotFoundError, ProviderRegistry
from app.repositories.conversations import ConversationRepository
from app.repositories.messages import MessageRepository
from app.services.chat import ChatService, ConversationNotFoundError
from tests.services.fake_provider import HANG, ScriptedProvider


def _model_registry(model_id: str, provider_id: str, context_window: int = 1_000_000) -> ModelRegistry:
    config = ModelConfig(
        id=model_id,
        provider=provider_id,
        provider_model_id=f"{provider_id}-v1",
        context_window=context_window,
        capabilities=ModelCapabilities(),
        pricing=PricingConfig(input_per_million=1.0, output_per_million=1.0),
    )
    return ModelRegistry({model_id: config})


async def _create_conversation(pool, tenant):
    async with tenant_connection(pool, tenant) as conn:
        return await ConversationRepository(conn, tenant).create()


async def _load_messages(pool, tenant, conversation_id):
    async with tenant_connection(pool, tenant) as conn:
        return await MessageRepository(conn, tenant).list_for_conversation(conversation_id)


# --- prepare_turn -------------------------------------------------------------


async def test_prepare_turn_resolves_provider_model_id_not_internal_id(pool, tenant_a):
    # Regression test for a real bug: a fake provider doesn't care what
    # string CompletionRequest.model holds, so no ScriptedProvider-based
    # test can catch sending the wrong one -- this was only found via live
    # testing against the real Anthropic API, which correctly 404'd on the
    # internal id "claude-sonnet" (it only knows "claude-sonnet-5"). This
    # test at least locks in that prepare_turn does the resolution, even
    # though it can't prove a real provider accepts the result.
    conversation = await _create_conversation(pool, tenant_a)
    fake_provider = ScriptedProvider("fake", [])
    models = _model_registry("internal-id", "fake")
    assert models.get("internal-id").provider_model_id == "fake-v1"
    service = ChatService(models, _registry_with(fake_provider))

    async with tenant_connection(pool, tenant_a) as conn:
        prepared = await service.prepare_turn(conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="hi", model_id="internal-id")

    assert prepared.request.model == "fake-v1"  # the provider model id, never "internal-id"
    assert prepared.model_id == "internal-id"  # but persistence still uses the internal id


async def test_prepare_turn_persists_user_message_before_any_streaming(pool, tenant_a):
    conversation = await _create_conversation(pool, tenant_a)
    fake_provider = ScriptedProvider("fake", [])
    service = ChatService(_model_registry("fake-model", "fake"), _registry_with(fake_provider))

    async with tenant_connection(pool, tenant_a) as conn:
        await service.prepare_turn(conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="hello", model_id="fake-model")

    messages = await _load_messages(pool, tenant_a, conversation.id)
    assert len(messages) == 1
    assert messages[0].role.value == "user"
    assert messages[0].content[0].text == "hello"


async def test_prepare_turn_raises_for_unknown_conversation(pool, tenant_a):
    service = ChatService(_model_registry("fake-model", "fake"), _registry_with(ScriptedProvider("fake", [])))
    async with tenant_connection(pool, tenant_a) as conn:
        with pytest.raises(ConversationNotFoundError):
            await service.prepare_turn(conn=conn, tenant=tenant_a, conversation_id=uuid4(), user_content="hi", model_id="fake-model")


async def test_prepare_turn_raises_for_unknown_model(pool, tenant_a):
    conversation = await _create_conversation(pool, tenant_a)
    service = ChatService(_model_registry("fake-model", "fake"), _registry_with(ScriptedProvider("fake", [])))
    async with tenant_connection(pool, tenant_a) as conn:
        with pytest.raises(ModelNotFoundError):
            await service.prepare_turn(conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="hi", model_id="does-not-exist")


async def test_prepare_turn_raises_for_unavailable_provider(pool, tenant_a):
    conversation = await _create_conversation(pool, tenant_a)
    # Model configured for a provider that was never registered (e.g. its API key is missing).
    service = ChatService(_model_registry("fake-model", "unregistered-provider"), ProviderRegistry())
    async with tenant_connection(pool, tenant_a) as conn:
        with pytest.raises(ProviderNotFoundError):
            await service.prepare_turn(conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="hi", model_id="fake-model")


# --- stream_reply: persistence outcomes ---------------------------------------


def _registry_with(*providers: ScriptedProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    for provider in providers:
        registry.register(provider)
    return registry


async def test_stream_reply_persists_complete_assistant_message_on_success(pool, tenant_a):
    conversation = await _create_conversation(pool, tenant_a)
    fake_provider = ScriptedProvider(
        "fake", [TextDeltaEvent(text="Hel"), TextDeltaEvent(text="lo"), UsageEvent(usage=Usage()), DoneEvent(finish_reason=FinishReason.STOP)]
    )
    service = ChatService(_model_registry("fake-model", "fake"), _registry_with(fake_provider))

    async with tenant_connection(pool, tenant_a) as conn:
        prepared = await service.prepare_turn(conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="hi", model_id="fake-model")

    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]
    assert [type(e).__name__ for e in events] == ["TextDeltaEvent", "TextDeltaEvent", "UsageEvent", "DoneEvent"]

    messages = await _load_messages(pool, tenant_a, conversation.id)
    assert [m.role.value for m in messages] == ["user", "assistant"]
    assistant = messages[1]
    assert assistant.content[0].text == "Hello"
    assert assistant.status == "complete"
    assert assistant.model_id == "fake-model"


async def test_stream_reply_persists_interrupted_message_on_cancellation(pool, tenant_a):
    conversation = await _create_conversation(pool, tenant_a)
    fake_provider = ScriptedProvider("fake", [TextDeltaEvent(text="Partial answer"), HANG])
    service = ChatService(_model_registry("fake-model", "fake"), _registry_with(fake_provider))

    async with tenant_connection(pool, tenant_a) as conn:
        prepared = await service.prepare_turn(conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="hi", model_id="fake-model")

    async def _consume():
        async for _ in service.stream_reply(prepared, pool=pool, tenant=tenant_a):
            pass

    task = asyncio.ensure_future(_consume())
    await asyncio.sleep(0.05)  # let it emit the text delta and reach the hang point
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    messages = await _load_messages(pool, tenant_a, conversation.id)
    assert [m.role.value for m in messages] == ["user", "assistant"]
    assistant = messages[1]
    assert assistant.content[0].text == "Partial answer"
    assert assistant.status == "interrupted"  # never indistinguishable from a real completion


async def test_stream_reply_persists_nothing_when_error_arrives_before_any_text(pool, tenant_a):
    conversation = await _create_conversation(pool, tenant_a)
    fake_provider = ScriptedProvider("fake", [ErrorEvent(kind=ProviderErrorKind.RATE_LIMIT, message="slow down")])
    service = ChatService(_model_registry("fake-model", "fake"), _registry_with(fake_provider))

    async with tenant_connection(pool, tenant_a) as conn:
        prepared = await service.prepare_turn(conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="hi", model_id="fake-model")

    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]
    assert isinstance(events[0], ErrorEvent)

    messages = await _load_messages(pool, tenant_a, conversation.id)
    # The user's own message is still there -- only the (empty) assistant
    # reply is skipped, since there's nothing to persist.
    assert [m.role.value for m in messages] == ["user"]


async def test_stream_reply_persists_interrupted_when_partial_text_then_error(pool, tenant_a):
    conversation = await _create_conversation(pool, tenant_a)
    fake_provider = ScriptedProvider(
        "fake", [TextDeltaEvent(text="Some text"), ErrorEvent(kind=ProviderErrorKind.SERVER_ERROR, message="oops")]
    )
    service = ChatService(_model_registry("fake-model", "fake"), _registry_with(fake_provider))

    async with tenant_connection(pool, tenant_a) as conn:
        prepared = await service.prepare_turn(conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="hi", model_id="fake-model")

    [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]

    messages = await _load_messages(pool, tenant_a, conversation.id)
    assistant = messages[1]
    assert assistant.content[0].text == "Some text"
    assert assistant.status == "interrupted"  # a DoneEvent never arrived


# --- provider switching --------------------------------------------------------


async def test_provider_switches_between_turns_while_history_stays_coherent(pool, tenant_a):
    conversation = await _create_conversation(pool, tenant_a)
    provider_a = ScriptedProvider("provider-a", [TextDeltaEvent(text="reply from A"), DoneEvent(finish_reason=FinishReason.STOP)])
    provider_b = ScriptedProvider("provider-b", [TextDeltaEvent(text="reply from B"), DoneEvent(finish_reason=FinishReason.STOP)])
    registry = _registry_with(provider_a, provider_b)
    models = ModelRegistry(
        {
            "model-a": ModelConfig(
                id="model-a", provider="provider-a", provider_model_id="a-v1", context_window=1_000_000,
                capabilities=ModelCapabilities(), pricing=PricingConfig(input_per_million=1.0, output_per_million=1.0),
            ),
            "model-b": ModelConfig(
                id="model-b", provider="provider-b", provider_model_id="b-v1", context_window=1_000_000,
                capabilities=ModelCapabilities(), pricing=PricingConfig(input_per_million=1.0, output_per_million=1.0),
            ),
        }
    )
    service = ChatService(models, registry)

    async with tenant_connection(pool, tenant_a) as conn:
        prepared_1 = await service.prepare_turn(conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="turn one", model_id="model-a")
    [e async for e in service.stream_reply(prepared_1, pool=pool, tenant=tenant_a)]

    async with tenant_connection(pool, tenant_a) as conn:
        prepared_2 = await service.prepare_turn(conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="turn two", model_id="model-b")
    [e async for e in service.stream_reply(prepared_2, pool=pool, tenant=tenant_a)]

    # Provider B's request carries the FULL prior history -- turn one's user
    # message and provider A's own reply -- even though B never generated it.
    texts_sent_to_b = [block.text for m in prepared_2.request.messages for block in m.content]
    assert texts_sent_to_b == ["turn one", "reply from A", "turn two"]

    stored = await _load_messages(pool, tenant_a, conversation.id)
    assert [m.content[0].text for m in stored] == ["turn one", "reply from A", "turn two", "reply from B"]
    assert stored[1].model_id == "model-a"
    assert stored[3].model_id == "model-b"
