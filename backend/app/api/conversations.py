import logging
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.tenant import TenantContext, get_tenant_context
from app.db.pool import tenant_connection
from app.providers.contracts import StreamEvent
from app.providers.models import ModelNotFoundError
from app.providers.registry import ProviderNotFoundError
from app.repositories.conversations import ConversationRepository
from app.repositories.messages import MessageRepository
from app.schemas.conversations import (
    ConversationDetail,
    ConversationSummary,
    CreateConversationRequest,
    MessageResponse,
    SendMessageRequest,
)
from app.services.chat import ChatService, ConversationNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter()


def _to_summary(conversation) -> ConversationSummary:
    return ConversationSummary(id=conversation.id, title=conversation.title, created_at=conversation.created_at, updated_at=conversation.updated_at)


@router.post("/conversations", response_model=ConversationSummary)
async def create_conversation(
    body: CreateConversationRequest, request: Request, tenant: TenantContext = Depends(get_tenant_context)
) -> ConversationSummary:
    async with tenant_connection(request.app.state.pool, tenant) as conn:
        conversation = await ConversationRepository(conn, tenant).create(title=body.title)
    return _to_summary(conversation)


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_conversations(request: Request, tenant: TenantContext = Depends(get_tenant_context)) -> list[ConversationSummary]:
    async with tenant_connection(request.app.state.pool, tenant) as conn:
        conversations = await ConversationRepository(conn, tenant).list()
    return [_to_summary(c) for c in conversations]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: UUID, request: Request, tenant: TenantContext = Depends(get_tenant_context)
) -> ConversationDetail:
    async with tenant_connection(request.app.state.pool, tenant) as conn:
        conversation = await ConversationRepository(conn, tenant).get(conversation_id)
        if conversation is None:
            # Not-found, not 403 -- same reasoning as CP-01's tenant
            # isolation tests: we don't want to confirm to tenant B that a
            # conversation with this id exists at all for tenant A.
            raise HTTPException(status_code=404, detail="Conversation not found")
        messages = await MessageRepository(conn, tenant).list_for_conversation(conversation_id)

    summary = _to_summary(conversation)
    return ConversationDetail(
        **summary.model_dump(),
        messages=[
            MessageResponse(id=m.id, role=m.role.value, content=m.content, model_id=m.model_id, status=m.status, created_at=m.created_at)
            for m in messages
        ],
    )


def _format_sse(event: StreamEvent) -> str:
    # The event's own `type` discriminator (text_delta, tool_use_start,
    # ..., done, error -- see app/providers/contracts.py) IS the
    # provider-neutral SSE event name the assignment asks for; no separate
    # mapping table needed. The browser never sees a provider-shaped event.
    return f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: UUID, body: SendMessageRequest, request: Request, tenant: TenantContext = Depends(get_tenant_context)
) -> StreamingResponse:
    pool = request.app.state.pool
    chat_service: ChatService = request.app.state.chat_service

    # Everything that can fail with a real HTTP status happens here,
    # eagerly, awaited to completion -- not inside the generator below.
    # An async generator's body doesn't run at all until first iterated,
    # which for a StreamingResponse is after headers (status 200) are
    # already committed to the client; a 404/400/503 discovered only once
    # streaming has "started" would be indistinguishable from a mid-stream
    # failure. Splitting validation from generation is what keeps a bad
    # conversation id or model id a real 404/400, not an SSE error event.
    async with tenant_connection(pool, tenant) as conn:
        try:
            prepared = await chat_service.prepare_turn(
                conn=conn, tenant=tenant, conversation_id=conversation_id, user_content=body.content, model_id=body.model
            )
        except ConversationNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found") from exc
        except ModelNotFoundError as exc:
            raise HTTPException(status_code=400, detail=f"Unknown model '{exc.model_id}'") from exc
        except ProviderNotFoundError as exc:
            raise HTTPException(status_code=503, detail=f"Provider '{exc.provider_id}' is not configured") from exc

    async def event_source() -> AsyncIterator[str]:
        async for event in chat_service.stream_reply(prepared, pool=pool, tenant=tenant):
            yield _format_sse(event)

    return StreamingResponse(event_source(), media_type="text/event-stream")
