import random

import pytest

from app.providers.errors import ProviderErrorKind
from app.services.retry import FALLBACK_ELIGIBLE_KINDS, RETRYABLE_KINDS, compute_backoff_seconds


def test_retryable_kinds_are_exactly_rate_limit_and_server_error():
    assert RETRYABLE_KINDS == {ProviderErrorKind.RATE_LIMIT, ProviderErrorKind.SERVER_ERROR}


def test_fallback_eligible_kinds_include_timeout_but_retryable_does_not():
    assert ProviderErrorKind.TIMEOUT in FALLBACK_ELIGIBLE_KINDS
    assert ProviderErrorKind.TIMEOUT not in RETRYABLE_KINDS


@pytest.mark.parametrize(
    "kind",
    [ProviderErrorKind.AUTH, ProviderErrorKind.BAD_REQUEST, ProviderErrorKind.CONTEXT_LENGTH, ProviderErrorKind.CONTENT_FILTER],
)
def test_never_retryable_or_fallback_eligible_kinds(kind):
    assert kind not in RETRYABLE_KINDS
    assert kind not in FALLBACK_ELIGIBLE_KINDS


def test_backoff_is_bounded_by_max_delay():
    rng = random.Random(0)
    for attempt in range(1, 10):
        delay = compute_backoff_seconds(attempt, base_delay=0.5, max_delay=8.0, rng=rng)
        assert 0 <= delay <= 8.0


def test_backoff_grows_with_attempt_number_before_hitting_the_ceiling():
    # Using the max possible jitter value (the ceiling itself) at each
    # attempt to compare growth deterministically, rather than relying on
    # a specific random draw.
    class _MaxRng:
        def uniform(self, a, b):
            return b

    rng = _MaxRng()
    delays = [compute_backoff_seconds(attempt, base_delay=0.5, max_delay=100.0, rng=rng) for attempt in range(1, 5)]
    assert delays == [0.5, 1.0, 2.0, 4.0]  # base * 2**(attempt-1), well under the ceiling


def test_backoff_is_jittered_not_deterministic():
    rng = random.Random(42)
    delays = {compute_backoff_seconds(3, base_delay=0.5, max_delay=8.0, rng=rng) for _ in range(10)}
    assert len(delays) > 1  # not the same value every call


def test_backoff_rejects_non_positive_attempt():
    with pytest.raises(ValueError):
        compute_backoff_seconds(0, base_delay=0.5, max_delay=8.0)
