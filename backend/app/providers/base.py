"""The core Provider abstraction.

Application code (the future chat service, API routes) depends on this
ABC and nothing else -- never on ``anthropic``, ``google.genai``, or
``openai`` types. An adapter (CP-03) subclasses this once per provider and
is responsible for translating normalized requests into that provider's
SDK calls, and that provider's native responses/exceptions back into the
normalized contracts from ``messages.py``, ``contracts.py``, and
``errors.py``.
"""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.providers.contracts import CompletionRequest, CompletionResponse, StreamEvent
from app.providers.errors import ProviderError, ProviderErrorKind


class Provider(ABC):
    """``id`` (e.g. ``"anthropic"``) is how :class:`ProviderRegistry` and
    :class:`~app.providers.models.ModelConfig.provider` refer to this
    provider -- it must match the ``provider:`` value used in
    ``models.yaml`` for any model this provider serves."""

    id: str

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Non-streaming completion."""
        ...

    @abstractmethod
    def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        """Streaming completion. Implementations are async generators
        (``async def stream(...): yield ...``); callers do
        ``async for event in provider.stream(request): ...``."""
        ...

    async def embed(self, texts: list[str], *, model: str) -> list[list[float]]:
        """Not every provider -- or even every model within a provider --
        does embeddings. Rather than forcing every adapter to implement a
        method it has no real answer for (a fake empty-list return, or an
        ``NotImplementedError`` that's invisible to normalized error
        handling), the base class supplies a default that raises a
        normalized :class:`ProviderError`. A provider that *does* support
        embeddings overrides this method; one that doesn't needs to write
        no code at all and still fails predictably.

        The alternative considered was a separate ``EmbeddingProvider``
        protocol that only embedding-capable adapters implement. That's
        more type-safe (a caller can't even attempt `.embed()` on a
        `Provider` that doesn't statically support it) but it forces
        `ProviderRegistry` to hand back a union type, and callers to do an
        `isinstance` check before every embedding call -- which is exactly
        the kind of provider-shape branching this whole checkpoint exists
        to avoid. This design keeps one interface and one registry;
        `ModelConfig.capabilities.embeddings` already lets a caller check
        ahead of time if it wants to, and a caller that doesn't check gets
        a normalized error instead of an ad hoc one.
        """
        raise ProviderError(
            kind=ProviderErrorKind.UNSUPPORTED,
            message=f"provider '{self.id}' does not support embeddings",
            provider=self.id,
        )
