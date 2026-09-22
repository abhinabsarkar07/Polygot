from fastapi import APIRouter, Request

from app.schemas.models import ModelSummary

router = APIRouter()


@router.get("/models", response_model=list[ModelSummary])
async def list_models(request: Request) -> list[ModelSummary]:
    registry = request.app.state.model_registry
    return [
        ModelSummary(id=m.id, provider=m.provider, context_window=m.context_window, capabilities=m.capabilities)
        for m in registry.list()
    ]
