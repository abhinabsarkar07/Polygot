"""Tenant-scoped message storage.

`content` round-trips through the exact normalized `ContentBlock` union
CP-02 defined (`app/providers/messages.py`) -- not a bespoke persistence
shape -- so reconstructing the `Message` list a `CompletionRequest` needs
(see `app/services/chat.py`) is direct model validation, not a translation
layer of its own.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import TypeAdapter

from app.providers.messages import ContentBlock, Role
from app.repositories.base import TenantScopedRepository

_content_adapter: TypeAdapter[list[ContentBlock]] = TypeAdapter(list[ContentBlock])


@dataclass(frozen=True)
class StoredMessage:
    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    role: Role
    content: list[ContentBlock]
    model_id: str | None
    status: str
    created_at: datetime


class MessageRepository(TenantScopedRepository):
    async def create(
        self,
        *,
        conversation_id: UUID,
        role: Role,
        content: list[ContentBlock],
        model_id: str | None = None,
        status: str = "complete",
    ) -> StoredMessage:
        content_json = json.dumps([block.model_dump(mode="json") for block in content])
        row = await self._conn.fetchrow(
            """
            INSERT INTO messages (tenant_id, conversation_id, role, content, model_id, status)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6)
            RETURNING id, tenant_id, conversation_id, role, content, model_id, status, created_at
            """,
            self._tenant.id,
            conversation_id,
            role.value,
            content_json,
            model_id,
            status,
        )
        return self._from_row(row)

    async def list_for_conversation(self, conversation_id: UUID) -> list[StoredMessage]:
        # No "AND tenant_id = ..." here either, same reasoning as
        # ConversationRepository.get -- RLS already restricts this to the
        # current tenant's rows regardless of which conversation_id was
        # asked for, including a conversation_id belonging to another
        # tenant (which simply matches zero rows, not an error).
        rows = await self._conn.fetch(
            "SELECT id, tenant_id, conversation_id, role, content, model_id, status, created_at "
            "FROM messages WHERE conversation_id = $1 ORDER BY created_at",
            conversation_id,
        )
        return [self._from_row(row) for row in rows]

    def _from_row(self, row) -> StoredMessage:
        content = _content_adapter.validate_python(json.loads(row["content"]))
        return StoredMessage(
            id=row["id"],
            tenant_id=row["tenant_id"],
            conversation_id=row["conversation_id"],
            role=Role(row["role"]),
            content=content,
            model_id=row["model_id"],
            status=row["status"],
            created_at=row["created_at"],
        )
