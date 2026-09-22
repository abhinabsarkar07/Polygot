from fastapi import APIRouter, Depends, Request

from app.core.tenant import TenantContext, get_tenant_context
from app.db.pool import tenant_connection
from app.repositories.usage import UsageRepository
from app.schemas.usage import ProviderUsageSummaryResponse, UsageSummaryResponse

router = APIRouter()


@router.get("/usage/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(request: Request, tenant: TenantContext = Depends(get_tenant_context)) -> UsageSummaryResponse:
    # Tenant-scoped by construction -- tenant_connection() + RLS, the same
    # boundary every other endpoint uses. Never aggregated across tenants.
    async with tenant_connection(request.app.state.pool, tenant) as conn:
        summaries = await UsageRepository(conn, tenant).summary_by_provider()

    return UsageSummaryResponse(
        providers=[
            ProviderUsageSummaryResponse(
                provider=s.provider,
                total_cost_usd=str(s.total_cost_usd),
                average_latency_ms=s.average_latency_ms,
                request_count=s.request_count,
            )
            for s in summaries
        ]
    )
