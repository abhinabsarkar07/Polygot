"""ModelRegistry: known models resolve from the real shipped config,
unknown ids fail predictably, and capability checks work generically --
never via `if provider == "..."` or `if model_id.startswith("claude")`.
"""

from pathlib import Path

import pytest

from app.providers import ModelNotFoundError, ModelRegistry

FIXTURE_YAML = Path(__file__).parent / "fixture_models.yaml"


@pytest.fixture
def registry() -> ModelRegistry:
    return ModelRegistry.from_yaml(FIXTURE_YAML)


def test_known_model_resolves_with_provider_and_provider_model_id(registry):
    config = registry.get("test-chat-model")
    assert config.provider == "fake"
    assert config.provider_model_id == "test-provider-v1"
    assert config.context_window == 100_000


def test_unknown_model_fails_predictably(registry):
    with pytest.raises(ModelNotFoundError) as exc_info:
        registry.get("does-not-exist")
    assert exc_info.value.model_id == "does-not-exist"


def test_capability_check_requires_no_provider_specific_branching(registry):
    # Generic capability-aware selection: "which configured models support
    # tools?" -- answerable from config alone, no `if provider == "x"`.
    tool_capable = [m.id for m in registry.list() if m.capabilities.tools]
    assert tool_capable == ["test-chat-model"]


def test_model_without_tool_support_reports_that_via_capabilities(registry):
    config = registry.get("test-no-tools-model")
    assert config.capabilities.tools is False
    assert config.capabilities.streaming is True


def test_pricing_is_read_from_config_not_hardcoded(registry):
    config = registry.get("test-chat-model")
    assert config.pricing.input_per_million == 3.0
    assert config.pricing.output_per_million == 15.0
    assert config.pricing.cached_input_per_million is None


def test_shipped_production_config_loads_and_resolves_real_models():
    # The actual app/providers/models.yaml the application loads at
    # startup -- separate from the fixture above, which exists purely to
    # test ModelRegistry's own behavior in isolation.
    registry = ModelRegistry.from_yaml()
    claude = registry.get("claude-sonnet")
    assert claude.provider == "anthropic"
    assert claude.capabilities.vision is True
