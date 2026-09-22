from decimal import Decimal

from app.providers.contracts import Usage
from app.providers.models import PricingConfig
from app.services.cost import calculate_cost_usd


def test_input_only_cost():
    pricing = PricingConfig(input_per_million=2.0, output_per_million=10.0)
    usage = Usage(input_tokens=1_000_000, output_tokens=None)
    assert calculate_cost_usd(pricing, usage) == Decimal("2.000000")


def test_output_only_cost():
    pricing = PricingConfig(input_per_million=2.0, output_per_million=10.0)
    usage = Usage(input_tokens=None, output_tokens=500_000)
    assert calculate_cost_usd(pricing, usage) == Decimal("5.000000")


def test_combined_input_and_output_cost():
    pricing = PricingConfig(input_per_million=2.0, output_per_million=10.0)
    usage = Usage(input_tokens=100_000, output_tokens=50_000)
    # 100_000/1_000_000 * 2.00 + 50_000/1_000_000 * 10.00 = 0.20 + 0.50
    assert calculate_cost_usd(pricing, usage) == Decimal("0.700000")


def test_missing_usage_returns_none_not_zero():
    pricing = PricingConfig(input_per_million=2.0, output_per_million=10.0)
    usage = Usage()  # nothing reported at all
    assert calculate_cost_usd(pricing, usage) is None


def test_zero_tokens_reported_is_a_real_zero_cost_not_none():
    pricing = PricingConfig(input_per_million=2.0, output_per_million=10.0)
    usage = Usage(input_tokens=0, output_tokens=0)
    # `usage.input_tokens is None` is False here (it's 0, a real reported
    # value) -- calculable, and the calculation of an all-zero usage is a
    # real $0, correctly distinct from "we don't know" (None).
    assert calculate_cost_usd(pricing, usage) == Decimal("0.000000")


def test_output_pricing_none_is_skipped_not_a_crash():
    # An embeddings-only model's PricingConfig (CP-05) has no
    # output_per_million at all.
    pricing = PricingConfig(input_per_million=0.02, output_per_million=None)
    usage = Usage(input_tokens=1_000_000, output_tokens=500)  # output_tokens shouldn't happen for embeddings, but must not crash
    assert calculate_cost_usd(pricing, usage) == Decimal("0.020000")


def test_cached_input_tokens_are_not_priced_documented_limitation():
    pricing = PricingConfig(input_per_million=2.0, output_per_million=10.0, cached_input_per_million=0.20)
    with_cache = Usage(input_tokens=100_000, output_tokens=0, cached_input_tokens=900_000)
    without_cache = Usage(input_tokens=100_000, output_tokens=0, cached_input_tokens=None)
    # Same result regardless of cached_input_tokens -- see calculate_cost_usd's
    # own docstring for why this is a documented limitation, not an oversight.
    assert calculate_cost_usd(pricing, with_cache) == calculate_cost_usd(pricing, without_cache)


def test_decimal_precision_not_binary_float_rounding():
    pricing = PricingConfig(input_per_million=0.02, output_per_million=None)
    usage = Usage(input_tokens=333_333)
    cost = calculate_cost_usd(pricing, usage)
    assert isinstance(cost, Decimal)
    # 333_333 / 1_000_000 * 0.02 = 0.0066666... rounded half-up to 6dp
    assert cost == Decimal("0.006667")
