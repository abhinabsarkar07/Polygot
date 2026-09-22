"""Proves every required ProviderError category is representable, and that
the normalized error carries no provider-specific residue."""

import pytest

from app.providers import ProviderError, ProviderErrorKind


@pytest.mark.parametrize(
    "kind",
    [
        ProviderErrorKind.AUTH,
        ProviderErrorKind.RATE_LIMIT,
        ProviderErrorKind.CONTEXT_LENGTH,
        ProviderErrorKind.CONTENT_FILTER,
        ProviderErrorKind.TIMEOUT,
        ProviderErrorKind.SERVER_ERROR,
        ProviderErrorKind.BAD_REQUEST,
        ProviderErrorKind.UNKNOWN,
        ProviderErrorKind.UNSUPPORTED,
    ],
)
def test_every_required_error_category_is_representable(kind):
    error = ProviderError(kind=kind, message="something went wrong", provider="anthropic")
    assert error.kind is kind
    assert error.provider == "anthropic"
    assert str(error) == "something went wrong"


def test_provider_field_is_optional_metadata_not_required():
    error = ProviderError(kind=ProviderErrorKind.UNKNOWN, message="mystery failure")
    assert error.provider is None
