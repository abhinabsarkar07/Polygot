"""One centralized USD cost calculation, driven entirely by
``PricingConfig`` (app/providers/models.py) -- no price ever appears in
this function's body, only in ``models.yaml``.
"""

from decimal import ROUND_HALF_UP, Decimal

from app.providers.contracts import Usage
from app.providers.models import PricingConfig

_MILLION = Decimal(1_000_000)
_CENTS_PRECISION = Decimal("0.000001")  # six decimal places -- sub-cent costs are common at real usage volumes


def calculate_cost_usd(pricing: PricingConfig, usage: Usage) -> Decimal | None:
    """``None`` when there is nothing to price from (STEP 8: a failed
    generation with no reported usage gets no fabricated cost) -- not
    ``Decimal("0")``, which would falsely claim "this cost nothing" rather
    than "we don't know what this cost."

    **Cached tokens are recorded on the usage record but deliberately not
    priced here.** Anthropic and OpenAI disagree on whether
    ``cached_input_tokens`` is *additional* to ``input_tokens`` (Anthropic:
    separate ``cache_read_input_tokens``) or a *subset already included
    within it* (OpenAI: ``input_tokens_details.cached_tokens`` appears to
    be a breakdown of, not an addition to, ``input_tokens`` -- unconfirmed
    against a real response, no live OpenAI usage payload has been
    inspected). Guessing which relationship applies and pricing it anyway
    risks silently over- or under-charging by exactly the cached-token
    discount every time. Per STEP 7 ("do not invent a pricing rule"),
    that's left unpriced and documented rather than guessed -- see
    docs/DESIGN.md, "Cost Accounting".
    """
    if usage.input_tokens is None and usage.output_tokens is None:
        return None

    total = Decimal("0")
    if usage.input_tokens:
        total += Decimal(usage.input_tokens) / _MILLION * Decimal(str(pricing.input_per_million))
    if usage.output_tokens and pricing.output_per_million is not None:
        total += Decimal(usage.output_tokens) / _MILLION * Decimal(str(pricing.output_per_million))

    return total.quantize(_CENTS_PRECISION, rounding=ROUND_HALF_UP)
