"""Builds a :class:`ProviderRegistry` from whichever provider credentials
are actually configured.

Not wired into the FastAPI app yet -- there's no chat service to hand this
registry to until CP-04. It exists now so the "a missing key means one
provider is unavailable, not that the app fails to start" requirement is a
property of this one function, provable directly (see
tests/providers/test_wiring.py) rather than only observable by booting the
whole application.

This is also the one place a fourth provider's construction gets added --
see docs/DESIGN.md, "Adding a provider". Everything above this function
(ProviderRegistry.get(), ModelRegistry.get(), any future chat service)
stays exactly as generic as CP-02 left it.
"""

from app.core.config import Settings
from app.providers.anthropic_adapter import AnthropicAdapter
from app.providers.gemini_adapter import GeminiAdapter
from app.providers.openai_adapter import OpenAIAdapter
from app.providers.registry import ProviderRegistry


def build_provider_registry(settings: Settings) -> ProviderRegistry:
    registry = ProviderRegistry()
    if settings.anthropic_api_key:
        registry.register(AnthropicAdapter(api_key=settings.anthropic_api_key))
    if settings.gemini_api_key:
        registry.register(GeminiAdapter(api_key=settings.gemini_api_key))
    if settings.openai_api_key:
        registry.register(OpenAIAdapter(api_key=settings.openai_api_key))
    return registry
