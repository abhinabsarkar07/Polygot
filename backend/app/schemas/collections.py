"""Request/response shapes for collections and document upload. No
``tenant_id`` field anywhere -- same rule as app/schemas/conversations.py."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.services.chunking import MAX_CHUNK_SIZE, MIN_CHUNK_SIZE


class CreateCollectionRequest(BaseModel):
    name: str


class CollectionResponse(BaseModel):
    id: UUID
    name: str
    created_at: datetime


class DocumentResponse(BaseModel):
    id: UUID
    collection_id: UUID
    filename: str
    content_type: str
    status: str
    error: str | None
    created_at: datetime


class CollectionDetail(CollectionResponse):
    documents: list[DocumentResponse]


class UploadDocumentQuery(BaseModel):
    """Ingestion-time controls (STEP 25) -- chunk size/overlap are fixed
    for a document once it's indexed; changing them means re-uploading,
    never a query-time toggle. Sent as form fields alongside the file
    (multipart/form-data), not JSON -- see app/api/collections.py."""

    chunk_size: int = Field(default=1000, ge=MIN_CHUNK_SIZE, le=MAX_CHUNK_SIZE)
    overlap: int = Field(default=150, ge=0)
