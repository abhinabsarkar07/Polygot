from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.core.config import get_settings
from app.core.tenant import TenantContext, get_tenant_context
from app.db.pool import tenant_connection
from app.repositories.collections import CollectionRepository
from app.repositories.documents import DocumentRepository
from app.schemas.collections import CollectionDetail, CollectionResponse, CreateCollectionRequest, DocumentResponse
from app.services.chunking import MAX_CHUNK_SIZE, MIN_CHUNK_SIZE, ChunkingConfig
from app.services.extraction import kind_for_filename
from app.services.ingestion import IngestionService

router = APIRouter()


def _to_collection_response(collection) -> CollectionResponse:
    return CollectionResponse(id=collection.id, name=collection.name, created_at=collection.created_at)


def _to_document_response(document) -> DocumentResponse:
    return DocumentResponse(
        id=document.id,
        collection_id=document.collection_id,
        filename=document.filename,
        content_type=document.content_type,
        status=document.status,
        error=document.error,
        created_at=document.created_at,
    )


@router.post("/collections", response_model=CollectionResponse)
async def create_collection(
    body: CreateCollectionRequest, request: Request, tenant: TenantContext = Depends(get_tenant_context)
) -> CollectionResponse:
    async with tenant_connection(request.app.state.pool, tenant) as conn:
        collection = await CollectionRepository(conn, tenant).create(body.name)
    return _to_collection_response(collection)


@router.get("/collections", response_model=list[CollectionResponse])
async def list_collections(request: Request, tenant: TenantContext = Depends(get_tenant_context)) -> list[CollectionResponse]:
    async with tenant_connection(request.app.state.pool, tenant) as conn:
        collections = await CollectionRepository(conn, tenant).list()
    return [_to_collection_response(c) for c in collections]


@router.get("/collections/{collection_id}", response_model=CollectionDetail)
async def get_collection(
    collection_id: UUID, request: Request, tenant: TenantContext = Depends(get_tenant_context)
) -> CollectionDetail:
    async with tenant_connection(request.app.state.pool, tenant) as conn:
        collection = await CollectionRepository(conn, tenant).get(collection_id)
        if collection is None:
            # Not-found, not 403 -- same reasoning as every other
            # tenant-scoped lookup in this codebase.
            raise HTTPException(status_code=404, detail="Collection not found")
        documents = await DocumentRepository(conn, tenant).list_for_collection(collection_id)

    return CollectionDetail(
        **_to_collection_response(collection).model_dump(), documents=[_to_document_response(d) for d in documents]
    )


@router.post("/collections/{collection_id}/documents", response_model=DocumentResponse)
async def upload_document(
    collection_id: UUID,
    request: Request,
    file: UploadFile = File(...),
    chunk_size: int = Form(default=1000, ge=MIN_CHUNK_SIZE, le=MAX_CHUNK_SIZE),
    overlap: int = Form(default=150, ge=0),
    tenant: TenantContext = Depends(get_tenant_context),
) -> DocumentResponse:
    settings = get_settings()
    pool = request.app.state.pool

    async with tenant_connection(pool, tenant) as conn:
        collection = await CollectionRepository(conn, tenant).get(collection_id)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    filename = file.filename or "upload"
    if kind_for_filename(filename) is None:
        # STEP 4: extension-driven, not the client-declared content-type
        # alone -- see app/services/extraction.py's module docstring for
        # why. A mismatched/spoofed content-type on a supported extension
        # doesn't get special treatment either way: extraction itself
        # will fail loudly on bytes that aren't what the extension claims.
        raise HTTPException(status_code=400, detail=f"Unsupported file type for '{filename}'. Use .txt, .md, or .pdf.")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"File exceeds the {settings.max_upload_bytes:,}-byte upload limit")

    try:
        chunking = ChunkingConfig(chunk_size=chunk_size, overlap=overlap)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ingestion: IngestionService = request.app.state.ingestion_service
    document = await ingestion.ingest(
        pool=pool,
        tenant=tenant,
        collection_id=collection_id,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        data=data,
        chunking=chunking,
    )
    return _to_document_response(document)
