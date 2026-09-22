"""A small, purpose-built wrapper over :meth:`Provider.embed` (CP-02)
rather than a second, parallel embedding-provider registry.

CP-02 already defined ``embed()`` as part of the ``Provider`` interface
itself, specifically anticipating this: "a provider method that raises a
normalized unsupported capability error" for providers that don't embed,
overridden by ones that do (see ``OpenAIAdapter.embed()``). Building a
separate ``EmbeddingProvider`` class hierarchy here would duplicate
``ProviderRegistry``'s credential-driven availability and
``ProviderError`` normalization for no real benefit -- this class exists
only to give RAG code the two purpose-shaped methods it actually calls
(``embed_documents`` / ``embed_query``) without RAG business logic
depending on ``openai`` types or a provider id string directly.
"""

from app.providers.models import ModelConfig
from app.providers.registry import ProviderRegistry


class EmbeddingService:
    def __init__(self, provider_registry: ProviderRegistry, model_config: ModelConfig) -> None:
        if not model_config.capabilities.embeddings:
            raise ValueError(f"model '{model_config.id}' is not configured for embeddings")
        # Provider resolution is deliberately lazy (not done here in
        # __init__): this service is constructed unconditionally at app
        # startup (app/main.py), same as ChatService. Resolving eagerly
        # would raise ProviderNotFoundError -- and crash startup -- the
        # moment OPENAI_API_KEY is unset, breaking the "a missing key
        # makes one provider/feature unavailable, never a startup crash"
        # rule every other provider-backed piece of this app follows
        # (see app/providers/wiring.py). Unset key -> RAG ingestion/
        # retrieval fails clearly when actually invoked, exactly like
        # selecting an unconfigured chat provider does.
        self._provider_registry = provider_registry
        self._model_config = model_config

    @property
    def dimension(self) -> int:
        assert self._model_config.dimension is not None  # guaranteed by ModelConfig for an embeddings-capable model
        return self._model_config.dimension

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        provider = self._provider_registry.get(self._model_config.provider)
        return await provider.embed(texts, model=self._model_config.provider_model_id)

    async def embed_query(self, text: str) -> list[float]:
        provider = self._provider_registry.get(self._model_config.provider)
        [vector] = await provider.embed([text], model=self._model_config.provider_model_id)
        return vector
