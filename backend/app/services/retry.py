"""Retry/fallback eligibility and backoff -- pure, deterministic, no
sleeping and no provider calls, so tests can exercise the real logic
without waiting seconds or mocking a provider (see tests/services/test_retry.py).

**Eligibility (STEP 10/15), stated once:**

- Retried (same model, same provider): ``rate_limit``, ``server_error``.
- Falls back to the next configured model (STEP 15) if retries are
  exhausted or the model has no retry budget left: ``rate_limit``,
  ``server_error``, ``timeout``. Timeout is fallback-eligible but not
  retried against the *same* provider -- a provider that just took 60s to
  not respond is a poor candidate to immediately ask again.
- Never retried, never falls back: ``auth``, ``bad_request``,
  ``context_length``, ``content_filter``, ``unknown``, ``unsupported``.
  Retrying a bad request or an auth failure wastes an attempt on
  something no amount of retrying fixes; silently trying a *different*
  model after a context-length error could quietly change the answer's
  grounding in a way nothing signals to the caller.
"""

import random

from app.providers.errors import ProviderErrorKind

RETRYABLE_KINDS = frozenset({ProviderErrorKind.RATE_LIMIT, ProviderErrorKind.SERVER_ERROR})
FALLBACK_ELIGIBLE_KINDS = frozenset({ProviderErrorKind.RATE_LIMIT, ProviderErrorKind.SERVER_ERROR, ProviderErrorKind.TIMEOUT})


def compute_backoff_seconds(attempt: int, *, base_delay: float, max_delay: float, rng: random.Random | None = None) -> float:
    """"Full jitter" (the AWS-recommended shape, not a fixed jitter added
    on top of a deterministic delay): a uniformly random value between 0
    and ``min(max_delay, base_delay * 2**attempt)``. ``attempt`` is
    1-indexed (the delay *before* the first retry, i.e. after the first
    failure, is ``attempt=1``).
    """
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    r = rng or random
    ceiling = min(max_delay, base_delay * (2 ** (attempt - 1)))
    return r.uniform(0, ceiling)
