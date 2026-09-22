"""Orchestrates one chat turn: validate + persist the user message, resolve
model/provider generically, stream the reply (with retry/fallback/timeout
resilience and usage/cost accounting), persist the assistant message and
a usage record. No provider-specific branching lives here -- see
``app/providers/registry.py`` and the adapters for why that's possible.

Split into two phases on purpose:

1. :meth:`prepare_turn` -- everything that can fail with a clear HTTP
   error (unknown conversation, unknown model, unavailable provider) and
   everything that must be durable *before* any token generation starts
   (the user's own message). Runs to completion inside one short
   ``tenant_connection``, committed before returning.
2. :meth:`stream_reply` -- the actual token stream. Opens **no** database
   connection for the duration of generation (which can run for seconds to
   minutes) -- only a second short connection at the very end, to persist
   the assistant's reply and usage record. This is also what makes
   cancellation safe: a single connection/transaction spanning the whole
   turn would roll back the already-committed user message the moment a
   cancellation unwound through it. Two short, independent transactions
   means the user message survives cancellation no matter when it happens.

**CP-06 streaming retry/fallback boundary, stated once:** retry (same
model) and fallback (next configured model) are only attempted *before
any visible output has streamed to the browser* (``accumulated_text ==
""``). The instant a real ``TextDeltaEvent`` has been yielded, a failure
is surfaced as a normalized ``ErrorEvent`` and nothing more is attempted
-- silently retrying or falling back after partial output risks
duplicated or inconsistently-sourced text reaching the user with no
signal that anything went wrong (see app/services/retry.py's module
docstring for the exact eligibility rules).
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

from app.core.tenant import TenantContext
from app.db.pool import tenant_connection
from app.providers.base import Provider
from app.providers.contracts import CompletionRequest, DoneEvent, ErrorEvent, FinishReason, StreamEvent, TextDeltaEvent, Usage, UsageEvent
from app.providers.errors import ProviderError, ProviderErrorKind, safe_message
from app.providers.messages import Message, Role, TextBlock
from app.providers.models import ModelConfig, ModelRegistry
from app.providers.registry import ProviderRegistry
from app.repositories.chunks import ChunkRepository
from app.repositories.conversations import ConversationRepository
from app.repositories.messages import MessageRepository
from app.repositories.usage import UsageRepository
from app.services.context_window import estimate_tokens, trim_to_context_window
from app.services.cost import calculate_cost_usd
from app.services.rag import NO_EVIDENCE_MESSAGE, CitedSource, SourcesEvent, assign_source_ids, build_grounded_system_prompt, extract_valid_citations
from app.services.retrieval import DEFAULT_SIMILARITY_THRESHOLD, DEFAULT_TOP_K, RetrievalService
from app.services.retry import FALLBACK_ELIGIBLE_KINDS, RETRYABLE_KINDS, compute_backoff_seconds

logger = logging.getLogger(__name__)

# Not user-configurable in CP-04 -- there's no UI control for it yet (out
# of scope per the checkpoint), just a deliberate, documented default.
DEFAULT_MAX_OUTPUT_TOKENS = 4096


class ConversationNotFoundError(LookupError):
    def __init__(self, conversation_id: UUID) -> None:
        super().__init__(f"no conversation '{conversation_id}' for this tenant")
        self.conversation_id = conversation_id


class RagUnavailableError(LookupError):
    """A collection_id was supplied but no embedding-capable provider is
    configured (see EmbeddingService's lazy resolution) -- distinct from
    ProviderNotFoundError so the API route can give a specific, honest
    error rather than a generic "provider not configured" that would
    misleadingly point at the *chat* model."""


class FallbackEvent(BaseModel):
    """Application-level SSE event (STEP 17), not a CP-02 StreamEvent --
    structurally compatible with _format_sse the same way SourcesEvent is."""

    type: Literal["fallback"] = "fallback"
    from_model: str
    to_model: str


@dataclass(frozen=True)
class RetryConfig:
    max_retries: int
    base_delay_seconds: float
    max_delay_seconds: float


@dataclass
class PreparedTurn:
    conversation_id: UUID
    model_id: str
    request: CompletionRequest
    model_config: ModelConfig
    # None: this turn has no collection selected (ordinary CP-04 chat,
    # unaffected). []: a collection was selected but nothing survived the
    # similarity threshold. Non-empty: real, retrieved, citable evidence.
    sources: list[CitedSource] | None = None
    # Set only for the "no evidence passed threshold" short-circuit
    # (STEP 23) -- when set, stream_reply never calls the provider at all.
    deterministic_text: str | None = None


class _Halt(Exception):
    """Internal control-flow signal only (never raised to a caller): the
    turn is done -- either succeeded or already surfaced a terminal
    ErrorEvent -- stop trying every remaining candidate/retry."""


class ChatService:
    def __init__(
        self,
        model_registry: ModelRegistry,
        provider_registry: ProviderRegistry,
        retrieval: RetrievalService | None = None,
        retry_config: RetryConfig | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self._models = model_registry
        self._providers = provider_registry
        self._retrieval = retrieval
        self._retry = retry_config or RetryConfig(max_retries=2, base_delay_seconds=0.5, max_delay_seconds=8.0)
        self._timeout_seconds = timeout_seconds
        # Overridable in tests only, so retry backoff tests don't actually
        # sleep for real seconds -- see tests/services/test_chat_service_resilience.py.
        self.sleep_fn = asyncio.sleep

    async def prepare_turn(
        self,
        *,
        conn: asyncpg.Connection,
        tenant: TenantContext,
        conversation_id: UUID,
        user_content: str,
        model_id: str,
        collection_id: UUID | None = None,
        top_k: int = DEFAULT_TOP_K,
        similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> PreparedTurn:
        # Raises ModelNotFoundError / ProviderNotFoundError (both already
        # defined, both already safe-to-surface LookupErrors) if the
        # caller asked for a model/provider that doesn't exist or isn't
        # configured -- the API route maps these to 400/503 before any
        # streaming response has started.
        model_config = self._models.get(model_id)
        self._providers.get(model_config.provider)  # validated eagerly; resolved again per attempt in stream_reply

        conversations = ConversationRepository(conn, tenant)
        conversation = await conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)

        messages = MessageRepository(conn, tenant)
        await messages.create(conversation_id=conversation_id, role=Role.USER, content=[TextBlock(text=user_content)])
        await conversations.touch(conversation_id)

        sources: list[CitedSource] | None = None
        deterministic_text: str | None = None
        system_prompt: str | None = None
        reserved_tokens = DEFAULT_MAX_OUTPUT_TOKENS

        if collection_id is not None:
            if self._retrieval is None:
                raise RagUnavailableError("no embedding-capable provider is configured")
            retrieved = await self._retrieval.retrieve(
                chunk_repository=ChunkRepository(conn, tenant),
                collection_id=collection_id,
                query=user_content,
                top_k=top_k,
                similarity_threshold=similarity_threshold,
            )
            sources = assign_source_ids(retrieved)
            if not sources:
                # STEP 23: retrieval itself already tells us there is no
                # grounding evidence -- the provider is never called, so
                # nothing is spent generating a response we'd have had to
                # discard anyway.
                deterministic_text = NO_EVIDENCE_MESSAGE
            else:
                system_prompt = build_grounded_system_prompt(sources)
                reserved_tokens += estimate_tokens(Message(role=Role.USER, content=[TextBlock(text=system_prompt)]))

        history = await messages.list_for_conversation(conversation_id)
        normalized_history = [Message(role=m.role, content=m.content) for m in history]
        trimmed = trim_to_context_window(
            normalized_history,
            context_window=model_config.context_window,
            reserved_output_tokens=reserved_tokens,
        )

        # CompletionRequest.model is what an adapter sends upstream as-is
        # (confirmed against a live 404 from Anthropic during manual
        # verification: "model: claude-sonnet" -- Anthropic has no such
        # model, only "claude-sonnet-5"). Internal ids are for
        # application-facing surfaces (SendMessageRequest.model,
        # ModelRegistry's keys) -- by the time a request reaches a
        # Provider, this field must already be model_config.provider_model_id.
        # PreparedTurn.model_id stays the internal id -- that's what gets
        # persisted on the assistant message for observability (see
        # docs/DESIGN.md, "Provider Switching").
        request = CompletionRequest(
            model=model_config.provider_model_id, messages=trimmed, system=system_prompt, max_tokens=DEFAULT_MAX_OUTPUT_TOKENS
        )
        return PreparedTurn(
            conversation_id=conversation_id,
            model_id=model_id,
            model_config=model_config,
            request=request,
            sources=sources,
            deterministic_text=deterministic_text,
        )

    async def stream_reply(self, prepared: PreparedTurn, *, pool: asyncpg.Pool, tenant: TenantContext) -> AsyncIterator[StreamEvent]:
        if prepared.sources is not None:
            yield SourcesEvent(sources=prepared.sources)

        if prepared.deterministic_text is not None:
            # A deterministic, application-generated reply never calls a
            # provider at all -- no usage record either; one would imply a
            # provider interaction that didn't happen (see module docstring).
            yield TextDeltaEvent(text=prepared.deterministic_text)
            yield DoneEvent(finish_reason=FinishReason.STOP)
            await self._persist_assistant_message(pool, tenant, prepared, prepared.deterministic_text, prepared.model_id, status="complete")
            return

        candidate_ids = [prepared.model_id, *prepared.model_config.fallback_model_ids]
        request_start = time.monotonic()

        accumulated_text = ""
        ttft_ms: float | None = None
        usage: Usage | None = None
        finish_reason: FinishReason | None = None
        retry_count = 0
        fallback_used = False
        final_model_id = prepared.model_id
        final_provider_id = ""
        status = "interrupted"

        try:
            for candidate_index, candidate_id in enumerate(candidate_ids):
                candidate_config = self._models.get(candidate_id)
                provider = self._providers.get(candidate_config.provider)
                request = _retarget(prepared.request, candidate_config.provider_model_id)

                if candidate_index > 0:
                    yield FallbackEvent(from_model=prepared.model_id, to_model=candidate_id)

                attempt = 0
                while True:
                    try:
                        async for event in self._stream_one_attempt(provider, request):
                            if isinstance(event, TextDeltaEvent):
                                if ttft_ms is None:
                                    ttft_ms = (time.monotonic() - request_start) * 1000
                                accumulated_text += event.text
                            elif isinstance(event, UsageEvent):
                                usage = event.usage
                            elif isinstance(event, DoneEvent):
                                finish_reason = event.finish_reason
                                status = "complete"
                            yield event
                        final_model_id = candidate_id
                        final_provider_id = provider.id
                        raise _Halt
                    except ProviderError as exc:
                        if accumulated_text:
                            # Visible output already streamed -- never
                            # retry or fall back past this point.
                            yield ErrorEvent(kind=exc.kind, message=safe_message(exc.kind))
                            final_model_id, final_provider_id = candidate_id, provider.id
                            raise _Halt from exc
                        if exc.kind in RETRYABLE_KINDS and attempt < self._retry.max_retries:
                            attempt += 1
                            retry_count += 1
                            await self.sleep_fn(
                                compute_backoff_seconds(
                                    attempt, base_delay=self._retry.base_delay_seconds, max_delay=self._retry.max_delay_seconds
                                )
                            )
                            continue
                        if exc.kind in FALLBACK_ELIGIBLE_KINDS and candidate_index + 1 < len(candidate_ids):
                            fallback_used = True
                            break  # to the next candidate model
                        yield ErrorEvent(kind=exc.kind, message=safe_message(exc.kind))
                        final_model_id, final_provider_id = candidate_id, provider.id
                        raise _Halt from exc
        except _Halt:
            pass
        except asyncio.CancelledError:
            # See docs/DESIGN.md, "Cancellation" for the anyio cancel-scope
            # reasoning behind shield() here -- unchanged from CP-04.
            total_latency_ms = (time.monotonic() - request_start) * 1000
            if accumulated_text:
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(
                        self._persist_turn_results(
                            pool, tenant, prepared, accumulated_text, "interrupted", final_model_id or prepared.model_id,
                            final_provider_id, ttft_ms, total_latency_ms, usage, finish_reason, retry_count, fallback_used,
                        )
                    )
            raise
        except Exception:
            logger.exception("unexpected error while streaming for conversation %s", prepared.conversation_id)
            yield ErrorEvent(kind=ProviderErrorKind.UNKNOWN, message="An unexpected error occurred.")

        total_latency_ms = (time.monotonic() - request_start) * 1000
        if accumulated_text:
            if prepared.sources:
                # Belt-and-suspenders check (STEP 21) on top of the
                # structural guarantee that already prevents fabricated
                # citations -- see app/services/rag.py's module docstring.
                cited = extract_valid_citations(accumulated_text, prepared.sources)
                logger.debug("cited sources for conversation %s: %s", prepared.conversation_id, cited)
            await self._persist_turn_results(
                pool, tenant, prepared, accumulated_text, status, final_model_id, final_provider_id,
                ttft_ms, total_latency_ms, usage, finish_reason, retry_count, fallback_used,
            )

    async def _stream_one_attempt(self, provider: Provider, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        """One attempt against one provider, with an overall timeout.
        Converts a yielded ``ErrorEvent`` (CP-03 adapters never raise from
        ``stream()``) into a raised ``ProviderError`` so retry/fallback
        eligibility can be checked in exactly one place in the caller,
        regardless of whether the underlying failure was raised or
        yielded."""
        try:
            async with asyncio.timeout(self._timeout_seconds):
                async for event in provider.stream(request):
                    if isinstance(event, ErrorEvent):
                        raise ProviderError(kind=event.kind, message=event.message, provider=provider.id)
                    yield event
        except TimeoutError as exc:
            raise ProviderError(
                kind=ProviderErrorKind.TIMEOUT, message=safe_message(ProviderErrorKind.TIMEOUT), provider=provider.id
            ) from exc

    async def _persist_turn_results(
        self,
        pool: asyncpg.Pool,
        tenant: TenantContext,
        prepared: PreparedTurn,
        text: str,
        status: str,
        final_model_id: str,
        final_provider_id: str,
        ttft_ms: float | None,
        total_latency_ms: float,
        usage: Usage | None,
        finish_reason: FinishReason | None,
        retry_count: int,
        fallback_used: bool,
    ) -> None:
        cost_usd: Decimal | None = None
        if usage is not None:
            final_config = self._models.get(final_model_id)
            cost_usd = calculate_cost_usd(final_config.pricing, usage)

        async with tenant_connection(pool, tenant) as conn:
            await MessageRepository(conn, tenant).create(
                conversation_id=prepared.conversation_id, role=Role.ASSISTANT, content=[TextBlock(text=text)],
                model_id=final_model_id, status=status,
            )
            await UsageRepository(conn, tenant).create(
                conversation_id=prepared.conversation_id,
                provider=final_provider_id or self._models.get(final_model_id).provider,
                requested_model_id=prepared.model_id,
                final_model_id=final_model_id,
                ttft_ms=ttft_ms,
                total_latency_ms=total_latency_ms,
                input_tokens=usage.input_tokens if usage else None,
                output_tokens=usage.output_tokens if usage else None,
                cached_input_tokens=usage.cached_input_tokens if usage else None,
                reasoning_tokens=usage.reasoning_tokens if usage else None,
                cost_usd=cost_usd,
                finish_reason=finish_reason.value if finish_reason else None,
                retry_count=retry_count,
                fallback_used=fallback_used,
            )

    async def _persist_assistant_message(
        self, pool: asyncpg.Pool, tenant: TenantContext, prepared: PreparedTurn, text: str, model_id: str, *, status: str
    ) -> None:
        async with tenant_connection(pool, tenant) as conn:
            await MessageRepository(conn, tenant).create(
                conversation_id=prepared.conversation_id,
                role=Role.ASSISTANT,
                content=[TextBlock(text=text)],
                model_id=model_id,
                status=status,
            )


def _retarget(request: CompletionRequest, provider_model_id: str) -> CompletionRequest:
    """A fallback candidate reuses the exact same messages/system/max_tokens
    -- only the destination model string changes."""
    return CompletionRequest(model=provider_model_id, messages=request.messages, system=request.system, max_tokens=request.max_tokens)
