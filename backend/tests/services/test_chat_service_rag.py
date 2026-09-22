"""ChatService's RAG integration: grounded generation when evidence is
found, the deterministic "I don't know" short-circuit when it isn't (the
provider must never be called in that case), and confirmation that
ordinary CP-04 chat is completely unaffected when no collection is
selected.
"""

from app.db.pool import tenant_connection
from app.providers.contracts import DoneEvent, FinishReason, TextDeltaEvent
from app.providers.registry import ProviderRegistry
from app.repositories.chunks import ChunkRepository
from app.repositories.collections import CollectionRepository
from app.repositories.conversations import ConversationRepository
from app.repositories.documents import DocumentRepository
from app.services.chat import ChatService, RagUnavailableError
from app.services.embeddings import EmbeddingService
from app.services.rag import NO_EVIDENCE_MESSAGE, SourcesEvent
from app.services.retrieval import RetrievalService
from tests.services.fake_embedding_provider import FakeEmbeddingProvider
from tests.services.fake_provider import ScriptedProvider
from tests.services.test_chat_service import _model_registry, _registry_with
from tests.services.test_embeddings import _embedding_model_config
import pytest


async def _create_conversation(pool, tenant):
    async with tenant_connection(pool, tenant) as conn:
        return await ConversationRepository(conn, tenant).create()


async def _seed_collection(pool, tenant, *, text: str, vector: list[float], filename: str = "doc.txt"):
    async with tenant_connection(pool, tenant) as conn:
        collection = await CollectionRepository(conn, tenant).create("test collection")
        document = await DocumentRepository(conn, tenant).create(collection_id=collection.id, filename=filename, content_type="text/plain")
        await ChunkRepository(conn, tenant).create_many(
            collection_id=collection.id, document_id=document.id, texts=[text], embeddings=[vector], page_numbers=[None]
        )
        await DocumentRepository(conn, tenant).mark_ready(document.id)
    return collection


def _chat_service_with_rag(chat_events, query_vector, chunk_vectors_match: bool):
    chat_provider = ScriptedProvider("fake", chat_events)
    embed_provider = FakeEmbeddingProvider(
        dimension=1536, vectors={"what is the refund window?": query_vector}
    )
    provider_registry = ProviderRegistry()
    provider_registry.register(chat_provider)
    provider_registry.register(embed_provider)
    embeddings = EmbeddingService(provider_registry, _embedding_model_config(dimension=1536))
    retrieval = RetrievalService(embeddings)
    service = ChatService(_model_registry("fake-model", "fake"), provider_registry, retrieval=retrieval)
    return service, chat_provider


IDENTICAL = [1.0] + [0.0] * 1535
UNRELATED = [0.0, 1.0] + [0.0] * 1534


async def test_evidence_found_grounds_the_request_and_calls_the_provider(pool, tenant_a):
    conversation = await _create_conversation(pool, tenant_a)
    collection = await _seed_collection(pool, tenant_a, text="Refunds are available within 30 days.", vector=IDENTICAL, filename="policy.txt")
    service, provider = _chat_service_with_rag(
        [TextDeltaEvent(text="30 days [S1]."), DoneEvent(finish_reason=FinishReason.STOP)],
        query_vector=IDENTICAL,
        chunk_vectors_match=True,
    )

    async with tenant_connection(pool, tenant_a) as conn:
        prepared = await service.prepare_turn(
            conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="what is the refund window?",
            model_id="fake-model", collection_id=collection.id, top_k=5, similarity_threshold=0.3,
        )

    assert prepared.sources is not None and len(prepared.sources) == 1
    assert prepared.sources[0].filename == "policy.txt"
    assert prepared.request.system is not None
    assert "untrusted data" in prepared.request.system.lower()
    assert "Refunds are available within 30 days." in prepared.request.system

    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]
    assert isinstance(events[0], SourcesEvent)
    assert len(events[0].sources) == 1
    assert len(provider.received_requests) == 1  # the provider WAS called


async def test_no_evidence_never_calls_the_provider_and_answers_deterministically(pool, tenant_a):
    conversation = await _create_conversation(pool, tenant_a)
    # Seed a chunk that is NOT similar to the query at all.
    collection = await _seed_collection(pool, tenant_a, text="Unrelated content about something else entirely.", vector=UNRELATED)
    service, provider = _chat_service_with_rag([], query_vector=IDENTICAL, chunk_vectors_match=False)

    async with tenant_connection(pool, tenant_a) as conn:
        prepared = await service.prepare_turn(
            conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="what is the refund window?",
            model_id="fake-model", collection_id=collection.id, top_k=5, similarity_threshold=0.3,
        )

    assert prepared.sources == []
    assert prepared.deterministic_text == NO_EVIDENCE_MESSAGE

    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]
    assert isinstance(events[0], SourcesEvent)
    assert events[0].sources == []
    assert any(isinstance(e, TextDeltaEvent) and e.text == NO_EVIDENCE_MESSAGE for e in events)
    assert any(isinstance(e, DoneEvent) for e in events)
    assert len(provider.received_requests) == 0  # the provider was NEVER called -- no wasted generation

    # And the deterministic answer really was persisted, same as any other reply.
    async with tenant_connection(pool, tenant_a) as conn:
        from app.repositories.messages import MessageRepository

        messages = await MessageRepository(conn, tenant_a).list_for_conversation(conversation.id)
    assert messages[-1].content[0].text == NO_EVIDENCE_MESSAGE
    assert messages[-1].status == "complete"


async def test_ordinary_chat_without_collection_id_is_unaffected(pool, tenant_a):
    conversation = await _create_conversation(pool, tenant_a)
    service, provider = _chat_service_with_rag(
        [TextDeltaEvent(text="hi there"), DoneEvent(finish_reason=FinishReason.STOP)], query_vector=IDENTICAL, chunk_vectors_match=True
    )

    async with tenant_connection(pool, tenant_a) as conn:
        prepared = await service.prepare_turn(
            conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="hello", model_id="fake-model"
        )

    assert prepared.sources is None
    assert prepared.deterministic_text is None
    assert prepared.request.system is None  # no grounding prompt injected

    events = [e async for e in service.stream_reply(prepared, pool=pool, tenant=tenant_a)]
    assert not any(isinstance(e, SourcesEvent) for e in events)  # no sources event at all for plain chat
    assert len(provider.received_requests) == 1


async def test_rag_unavailable_when_no_retrieval_service_configured(pool, tenant_a):
    conversation = await _create_conversation(pool, tenant_a)
    collection = await _seed_collection(pool, tenant_a, text="x", vector=IDENTICAL)
    service = ChatService(_model_registry("fake-model", "fake"), _registry_with(ScriptedProvider("fake", [])))  # retrieval=None

    async with tenant_connection(pool, tenant_a) as conn:
        with pytest.raises(RagUnavailableError):
            await service.prepare_turn(
                conn=conn, tenant=tenant_a, conversation_id=conversation.id, user_content="hi",
                model_id="fake-model", collection_id=collection.id,
            )
