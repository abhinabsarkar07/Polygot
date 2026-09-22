"""ProviderRegistry: registered providers resolve by id, unknown ids fail
predictably."""

import pytest

from app.providers import ProviderNotFoundError, ProviderRegistry
from tests.providers.fake_provider import FakeProvider


def test_registered_provider_resolves_by_id():
    registry = ProviderRegistry()
    registry.register(FakeProvider())

    resolved = registry.get("fake")

    assert resolved.id == "fake"
    assert isinstance(resolved, FakeProvider)


def test_unknown_provider_id_fails_predictably():
    registry = ProviderRegistry()

    with pytest.raises(ProviderNotFoundError) as exc_info:
        registry.get("does-not-exist")

    assert exc_info.value.provider_id == "does-not-exist"


def test_list_ids_reflects_registered_providers():
    registry = ProviderRegistry()
    registry.register(FakeProvider())

    assert registry.list_ids() == ["fake"]
