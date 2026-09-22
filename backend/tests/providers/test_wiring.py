"""A missing provider key means that one provider is unavailable, not that
the app fails to start -- proven directly against build_provider_registry
rather than only observable by booting the whole application.
"""

from app.core.config import Settings
from app.providers.anthropic_adapter import AnthropicAdapter
from app.providers.gemini_adapter import GeminiAdapter
from app.providers.openai_adapter import OpenAIAdapter
from app.providers.registry import ProviderNotFoundError
from app.providers.wiring import build_provider_registry


def _settings(**overrides) -> Settings:
    return Settings(database_url="postgresql://unused/unused", **overrides)


def test_no_keys_configured_builds_an_empty_registry_without_raising():
    registry = build_provider_registry(_settings())
    assert registry.list_ids() == []


def test_selecting_an_unconfigured_provider_fails_clearly_not_silently():
    registry = build_provider_registry(_settings())
    try:
        registry.get("anthropic")
        raised = False
    except ProviderNotFoundError:
        raised = True
    assert raised


def test_only_providers_with_a_configured_key_are_registered():
    registry = build_provider_registry(_settings(anthropic_api_key="sk-test", gemini_api_key=None, openai_api_key=None))
    assert registry.list_ids() == ["anthropic"]
    assert isinstance(registry.get("anthropic"), AnthropicAdapter)


def test_all_three_keys_configured_registers_all_three_adapters():
    registry = build_provider_registry(
        _settings(anthropic_api_key="sk-test", gemini_api_key="gm-test", openai_api_key="oa-test")
    )
    assert set(registry.list_ids()) == {"anthropic", "gemini", "openai"}
    assert isinstance(registry.get("gemini"), GeminiAdapter)
    assert isinstance(registry.get("openai"), OpenAIAdapter)
