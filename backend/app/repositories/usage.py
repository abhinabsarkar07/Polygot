from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.repositories.base import TenantScopedRepository


@dataclass(frozen=True)
class UsageRecord:
    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    provider: str
    requested_model_id: str
    final_model_id: str
    ttft_ms: float | None
    total_latency_ms: float
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    reasoning_tokens: int | None
    cost_usd: Decimal | None
    finish_reason: str | None
    retry_count: int
    fallback_used: bool
    created_at: datetime


@dataclass(frozen=True)
class ProviderUsageSummary:
    provider: str
    total_cost_usd: Decimal
    average_latency_ms: float
    request_count: int


class UsageRepository(TenantScopedRepository):
    async def create(
        self,
        *,
        conversation_id: UUID,
        provider: str,
        requested_model_id: str,
        final_model_id: str,
        ttft_ms: float | None,
        total_latency_ms: float,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        reasoning_tokens: int | None,
        cost_usd: Decimal | None,
        finish_reason: str | None,
        retry_count: int,
        fallback_used: bool,
    ) -> UsageRecord:
        row = await self._conn.fetchrow(
            """
            INSERT INTO usage_records (
                tenant_id, conversation_id, provider, requested_model_id, final_model_id,
                ttft_ms, total_latency_ms, input_tokens, output_tokens, cached_input_tokens,
                reasoning_tokens, cost_usd, finish_reason, retry_count, fallback_used
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            RETURNING id, tenant_id, conversation_id, provider, requested_model_id, final_model_id,
                      ttft_ms, total_latency_ms, input_tokens, output_tokens, cached_input_tokens,
                      reasoning_tokens, cost_usd, finish_reason, retry_count, fallback_used, created_at
            """,
            self._tenant.id,
            conversation_id,
            provider,
            requested_model_id,
            final_model_id,
            ttft_ms,
            total_latency_ms,
            input_tokens,
            output_tokens,
            cached_input_tokens,
            reasoning_tokens,
            cost_usd,
            finish_reason,
            retry_count,
            fallback_used,
        )
        return UsageRecord(**row)

    async def summary_by_provider(self) -> list[ProviderUsageSummary]:
        # No explicit tenant_id filter -- RLS already restricts this
        # connection to the current tenant's own rows, same structural
        # guarantee as every other tenant-scoped query in this codebase.
        # COALESCE(SUM(cost_usd), 0) so a provider with only unpriced
        # (NULL-cost) records still shows up with $0 total rather than
        # NULL turning the whole aggregate NULL.
        rows = await self._conn.fetch(
            """
            SELECT provider,
                   COALESCE(SUM(cost_usd), 0) AS total_cost_usd,
                   AVG(total_latency_ms) AS average_latency_ms,
                   COUNT(*) AS request_count
            FROM usage_records
            GROUP BY provider
            ORDER BY provider
            """
        )
        return [
            ProviderUsageSummary(
                provider=row["provider"],
                total_cost_usd=row["total_cost_usd"],
                average_latency_ms=row["average_latency_ms"],
                request_count=row["request_count"],
            )
            for row in rows
        ]
