"""Orchestrates one chat turn: validate + persist the user message, resolve
model/provider generically, stream the reply, persist the assistant
message. No provider-specific branching lives here -- see
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
   the assistant's reply. This is also what makes cancellation safe: a
   single connection/transaction spanning the whole turn would roll back
   the already-committed user message the moment a cancellation unwound
   through it. Two short, independent transactions means the user message
   survives cancellation no matter when it happens.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from app.core.tenant import TenantContext
from app.db.pool import tenant_connection
from app.providers.base import Provider
from app.providers.contracts import CompletionRequest, DoneEvent, ErrorEvent, StreamEvent, TextDeltaEvent
from app.providers.errors import ProviderErrorKind
from app.providers.messages import Message, Role, TextBlock
from app.providers.models import ModelRegistry
from app.providers.registry import ProviderRegistry
from app.repositories.conversations import ConversationRepository
from app.repositories.messages import MessageRepository
from app.services.context_window import trim_to_context_window

logger = logging.getLogger(__name__)

# Not user-configurable in CP-04 -- there's no UI control for it yet (out
# of scope per the checkpoint), just a deliberate, documented default.
DEFAULT_MAX_OUTPUT_TOKENS = 4096


class ConversationNotFoundError(LookupError):
    def __init__(self, conversation_id: UUID) -> None:
        super().__init__(f"no conversation '{conversation_id}' for this tenant")
        self.conversation_id = conversation_id


@dataclass
class PreparedTurn:
    conversation_id: UUID
    model_id: str
    provider: Provider
    request: CompletionRequest


class ChatService:
    def __init__(self, model_registry: ModelRegistry, provider_registry: ProviderRegistry) -> None:
        self._models = model_registry
        self._providers = provider_registry

    async def prepare_turn(
        self,
        *,
        conn: asyncpg.Connection,
        tenant: TenantContext,
        conversation_id: UUID,
        user_content: str,
        model_id: str,
    ) -> PreparedTurn:
        # Raises ModelNotFoundError / ProviderNotFoundError (both already
        # defined, both already safe-to-surface LookupErrors) if the
        # caller asked for a model/provider that doesn't exist or isn't
        # configured -- the API route maps these to 400/503 before any
        # streaming response has started.
        model_config = self._models.get(model_id)
        provider = self._providers.get(model_config.provider)

        conversations = ConversationRepository(conn, tenant)
        conversation = await conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)

        messages = MessageRepository(conn, tenant)
        await messages.create(conversation_id=conversation_id, role=Role.USER, content=[TextBlock(text=user_content)])
        await conversations.touch(conversation_id)

        history = await messages.list_for_conversation(conversation_id)
        normalized_history = [Message(role=m.role, content=m.content) for m in history]
        trimmed = trim_to_context_window(
            normalized_history,
            context_window=model_config.context_window,
            reserved_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
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
        request = CompletionRequest(model=model_config.provider_model_id, messages=trimmed, max_tokens=DEFAULT_MAX_OUTPUT_TOKENS)
        return PreparedTurn(conversation_id=conversation_id, model_id=model_id, provider=provider, request=request)

    async def stream_reply(self, prepared: PreparedTurn, *, pool: asyncpg.Pool, tenant: TenantContext) -> AsyncIterator[StreamEvent]:
        accumulated_text = ""
        saw_done = False
        try:
            async for event in prepared.provider.stream(prepared.request):
                yield event
                if isinstance(event, TextDeltaEvent):
                    accumulated_text += event.text
                elif isinstance(event, DoneEvent):
                    saw_done = True
        except asyncio.CancelledError:
            # Cancellation reaches here (rather than being swallowed) via
            # the exact same property CP-03's adapters rely on: this
            # `except` clause catches Exception subclasses only, and
            # asyncio.CancelledError is a BaseException since Python 3.8.
            #
            # `asyncio.shield()` matters here, and is not just belt-and-
            # suspenders: found live (a real Stop mid-generation persisted
            # nothing at all, not even an interrupted row) that Starlette's
            # StreamingResponse cancels via an anyio cancel *scope*
            # (app/../starlette/responses.py::StreamingResponse.__call__),
            # and once that scope is cancelled, every subsequent `await` in
            # this task keeps re-raising CancelledError at its next
            # checkpoint -- including a plain `await` on the cleanup insert
            # itself, silently aborting it mid-write. `shield()` runs the
            # persist as a genuinely separate Task the scope doesn't reach,
            # so it actually completes before this generator re-raises.
            if accumulated_text:
                # `await shield(...)` still raises CancelledError back to
                # *us*, immediately -- that's correct, expected shield()
                # behavior, not a failure -- but the persist it wraps keeps
                # running detached underneath and does complete a moment
                # later (confirmed live: without shield(), the very same
                # write was silently cut off mid-INSERT by the same cancel
                # scope, every single time; with it, the interrupted row
                # reliably appears ~1-2s after the client disconnects).
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(
                        self._persist_assistant_message(pool, tenant, prepared, accumulated_text, status="interrupted")
                    )
            raise
        except Exception:
            logger.exception("unexpected error while streaming from provider '%s'", prepared.provider.id)
            yield ErrorEvent(kind=ProviderErrorKind.UNKNOWN, message="An unexpected error occurred.")
        else:
            if accumulated_text:
                status = "complete" if saw_done else "interrupted"
                await self._persist_assistant_message(pool, tenant, prepared, accumulated_text, status=status)

    async def _persist_assistant_message(
        self, pool: asyncpg.Pool, tenant: TenantContext, prepared: PreparedTurn, text: str, *, status: str
    ) -> None:
        async with tenant_connection(pool, tenant) as conn:
            await MessageRepository(conn, tenant).create(
                conversation_id=prepared.conversation_id,
                role=Role.ASSISTANT,
                content=[TextBlock(text=text)],
                model_id=prepared.model_id,
                status=status,
            )
