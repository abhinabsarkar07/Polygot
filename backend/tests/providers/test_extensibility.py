"""The most important test in CP-02.

Proves that generic, provider-agnostic application code -- resolve a model,
resolve its provider, call the Provider interface -- can drive a provider
it has never heard of, using only the model config + registries. This is
the exact shape the eventual chat service will use for every real adapter;
if a fake provider works through this path with zero special-casing, a
fourth real provider will too.
"""

from pathlib import Path

import pytest

from app.providers import (
    CompletionRequest,
    Message,
    ModelRegistry,
    ProviderError,
    ProviderErrorKind,
    ProviderRegistry,
    Role,
    TextBlock,
    TextDeltaEvent,
)
from tests.providers.fake_provider import FakeProvider

FIXTURE_YAML = Path(__file__).parent / "fixture_models.yaml"


def _generic_complete(model_id: str, text: str, *, model_registry: ModelRegistry, provider_registry: ProviderRegistry):
    """Stand-in for what the CP-04 chat service will actually do. Notice
    what's absent: no `if`/`elif` on provider name, no import of any
    concrete provider class -- only the two registries and the Provider
    interface they hand back."""
    model_config = model_registry.get(model_id)
    provider = provider_registry.get(model_config.provider)
    request = CompletionRequest(
        model=model_id,
        messages=[Message(role=Role.USER, content=[TextBlock(text=text)])],
        max_tokens=100,
    )
    return provider, request


@pytest.fixture
def model_registry() -> ModelRegistry:
    return ModelRegistry.from_yaml(FIXTURE_YAML)


@pytest.fixture
def provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(FakeProvider())
    return registry


async def test_generic_resolution_drives_an_unknown_provider_through_complete(model_registry, provider_registry):
    provider, request = _generic_complete(
        "test-chat-model", "hello", model_registry=model_registry, provider_registry=provider_registry
    )

    response = await provider.complete(request)

    assert response.content[0].text == "echo: hello"


async def test_generic_resolution_drives_an_unknown_provider_through_stream(model_registry, provider_registry):
    provider, request = _generic_complete(
        "test-chat-model", "hi there", model_registry=model_registry, provider_registry=provider_registry
    )

    text_deltas = [
        event.text
        async for event in provider.stream(request)
        if isinstance(event, TextDeltaEvent)
    ]

    assert "".join(text_deltas) == "echo: hi there "


async def test_provider_that_does_not_override_embed_fails_with_normalized_unsupported_error(provider_registry):
    provider = provider_registry.get("fake")

    with pytest.raises(ProviderError) as exc_info:
        await provider.embed(["some text"], model="test-chat-model")

    assert exc_info.value.kind is ProviderErrorKind.UNSUPPORTED
