import pytest

from app.providers.models import ModelCapabilities, ModelConfig, PricingConfig
from app.providers.registry import ProviderNotFoundError, ProviderRegistry
from app.services.embeddings import EmbeddingService
from tests.services.fake_embedding_provider import FakeEmbeddingProvider


def _embedding_model_config(provider: str = "fake-embed", dimension: int = 8) -> ModelConfig:
    return ModelConfig(
        id="test-embedding-model",
        provider=provider,
        provider_model_id="test-embedding-model-v1",
        context_window=8192,
        dimension=dimension,
        capabilities=ModelCapabilities(embeddings=True, streaming=False),
        pricing=PricingConfig(input_per_million=0.02, output_per_million=None),
    )


def test_rejects_a_model_not_configured_for_embeddings():
    registry = ProviderRegistry()
    chat_model = ModelConfig(
        id="chat-model", provider="fake-embed", provider_model_id="x", context_window=1000,
        capabilities=ModelCapabilities(embeddings=False), pricing=PricingConfig(input_per_million=1.0, output_per_million=1.0),
    )
    with pytest.raises(ValueError, match="not configured for embeddings"):
        EmbeddingService(registry, chat_model)


def test_dimension_property_reflects_model_config():
    registry = ProviderRegistry()
    service = EmbeddingService(registry, _embedding_model_config(dimension=8))
    assert service.dimension == 8


async def test_embed_documents_flow():
    provider = FakeEmbeddingProvider(dimension=8)
    registry = ProviderRegistry()
    registry.register(provider)
    service = EmbeddingService(registry, _embedding_model_config())

    vectors = await service.embed_documents(["hello", "world"])

    assert len(vectors) == 2
    assert all(len(v) == 8 for v in vectors)
    assert provider.embed_call_count == 1  # one batched call, not one per text


async def test_embed_documents_empty_list_short_circuits_without_calling_provider():
    provider = FakeEmbeddingProvider()
    registry = ProviderRegistry()
    registry.register(provider)
    service = EmbeddingService(registry, _embedding_model_config())

    assert await service.embed_documents([]) == []
    assert provider.embed_call_count == 0


async def test_embed_query_flow():
    provider = FakeEmbeddingProvider(dimension=8, vectors={"what is the refund policy?": [1.0] * 8})
    registry = ProviderRegistry()
    registry.register(provider)
    service = EmbeddingService(registry, _embedding_model_config())

    vector = await service.embed_query("what is the refund policy?")

    assert vector == [1.0] * 8


async def test_construction_does_not_require_the_provider_to_be_registered():
    # The key regression this test locks in: EmbeddingService must be
    # constructible even when its provider isn't registered (e.g. no
    # OPENAI_API_KEY) -- app/main.py builds it unconditionally at startup.
    registry = ProviderRegistry()  # nothing registered
    service = EmbeddingService(registry, _embedding_model_config())  # must not raise

    with pytest.raises(ProviderNotFoundError):
        await service.embed_query("hello")
