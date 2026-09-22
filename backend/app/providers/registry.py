"""Resolves a provider id (e.g. ``"anthropic"``) to a live :class:`Provider`
instance.

Application code never constructs an adapter directly and never branches
on provider name -- it asks the registry. Adding a fourth provider means
constructing its adapter once (at startup, wherever providers get wired
up) and calling ``registry.register(...)``; no generic code here or above
it changes.
"""

from app.providers.base import Provider


class ProviderNotFoundError(LookupError):
    def __init__(self, provider_id: str) -> None:
        super().__init__(f"no provider registered for id '{provider_id}'")
        self.provider_id = provider_id


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        self._providers[provider.id] = provider

    def get(self, provider_id: str) -> Provider:
        try:
            return self._providers[provider_id]
        except KeyError:
            raise ProviderNotFoundError(provider_id) from None

    def list_ids(self) -> list[str]:
        return list(self._providers.keys())
