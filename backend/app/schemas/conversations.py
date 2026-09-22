"""Request/response shapes for the conversation API.

Deliberately no ``tenant_id`` field anywhere here -- tenant identity comes
only from ``TenantContext`` (see app/core/tenant.py), never from the
request body. A client sending ``{"tenant_id": "tenant-b"}`` has that
field silently ignored by FastAPI's request validation (it isn't part of
any of these models), not honored.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.providers.messages import ContentBlock
from app.services.retrieval import DEFAULT_SIMILARITY_THRESHOLD, DEFAULT_TOP_K, MAX_TOP_K, MIN_TOP_K


class CreateConversationRequest(BaseModel):
    title: str | None = None


class ConversationSummary(BaseModel):
    id: UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class MessageResponse(BaseModel):
    id: UUID
    role: str
    content: list[ContentBlock]
    model_id: str | None
    status: str
    created_at: datetime


class ConversationDetail(ConversationSummary):
    messages: list[MessageResponse]


class SendMessageRequest(BaseModel):
    content: str
    # The browser's internal model id (e.g. "claude-sonnet"), resolved
    # server-side via ModelRegistry -- never a provider-native model
    # string. See app/services/chat.py::ChatService.prepare_turn.
    model: str

    # RAG (CP-05), all optional -- omitting collection_id is ordinary
    # CP-04 chat, unaffected. top_k/similarity_threshold are QUERY-time
    # controls (STEP 25) -- chunk size/overlap are INGESTION-time
    # settings instead (see UploadDocumentQuery below); re-querying the
    # same collection with different top_k/threshold never requires
    # re-indexing anything.
    collection_id: UUID | None = None
    top_k: int = Field(default=DEFAULT_TOP_K, ge=MIN_TOP_K, le=MAX_TOP_K)
    similarity_threshold: float = Field(default=DEFAULT_SIMILARITY_THRESHOLD, ge=-1.0, le=1.0)
