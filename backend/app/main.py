import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.collections import router as collections_router
from app.api.conversations import router as conversations_router
from app.api.health import router as health_router
from app.api.models import router as models_router
from app.api.usage import router as usage_router
from app.core.config import get_settings
from app.db.migrate import run_migrations
from app.db.pool import create_pool
from app.db.seed import ensure_dev_tenants
from app.providers.models import ModelRegistry
from app.providers.wiring import build_provider_registry
from app.services.chat import ChatService, RetryConfig
from app.services.embeddings import EmbeddingService
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService

# Internal model id of the one embedding model CP-05 configures -- see
# app/providers/models.yaml. A constant here, not scattered across the
# services that need it (mirrors how ChatService takes a resolved
# ModelRegistry rather than any service hardcoding a chat model id).
EMBEDDING_MODEL_ID = "text-embedding-3-small"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    pool = await create_pool(settings.database_url)
    app.state.pool = pool

    await run_migrations(pool)
    if settings.app_env == "development":
        await ensure_dev_tenants(pool)

    model_registry = ModelRegistry.from_yaml()
    provider_registry = build_provider_registry(settings)
    app.state.model_registry = model_registry
    app.state.provider_registry = provider_registry

    # Constructed unconditionally, same as every provider adapter --
    # EmbeddingService resolves its provider lazily (see its own
    # docstring), so this never crashes startup just because
    # OPENAI_API_KEY happens to be unset. RAG features are simply
    # unavailable (a clear 503, see RagUnavailableError) until it is.
    embedding_service = EmbeddingService(provider_registry, model_registry.get(EMBEDDING_MODEL_ID))
    retrieval_service = RetrievalService(embedding_service)
    app.state.ingestion_service = IngestionService(embedding_service)
    app.state.chat_service = ChatService(
        model_registry,
        provider_registry,
        retrieval=retrieval_service,
        retry_config=RetryConfig(
            max_retries=settings.retry_max_retries,
            base_delay_seconds=settings.retry_base_delay_seconds,
            max_delay_seconds=settings.retry_max_delay_seconds,
        ),
        timeout_seconds=settings.provider_request_timeout_seconds,
    )

    yield

    await pool.close()


app = FastAPI(title="Polyglot", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    # A single configured origin, not "*" -- the frontend origin is the
    # only caller this API is meant to serve from a browser.
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Ordinary exceptions must never leak stack traces, DB DSNs, or other
    # internals to the client. The real exception still goes to the
    # server log for us to debug.
    logger.exception("Unhandled exception while handling %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(health_router, prefix="/api")
app.include_router(models_router, prefix="/api")
app.include_router(conversations_router, prefix="/api")
app.include_router(collections_router, prefix="/api")
app.include_router(usage_router, prefix="/api")
