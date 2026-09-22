"""Request/response shapes for the conversation API.

Deliberately no ``tenant_id`` field anywhere here -- tenant identity comes
only from ``TenantContext`` (see app/core/tenant.py), never from the
request body. A client sending ``{"tenant_id": "tenant-b"}`` has that
field silently ignored by FastAPI's request validation (it isn't part of
any of these models), not honored.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.providers.messages import ContentBlock


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
