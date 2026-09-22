from pydantic import BaseModel


class ProviderUsageSummaryResponse(BaseModel):
    provider: str
    total_cost_usd: str  # Decimal serialized as a string -- never a float, no silent precision loss
    average_latency_ms: float
    request_count: int


class UsageSummaryResponse(BaseModel):
    providers: list[ProviderUsageSummaryResponse]
