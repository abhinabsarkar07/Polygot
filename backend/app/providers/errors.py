"""Normalized provider errors.

Adapters (CP-03) catch each provider SDK's native exceptions --
``anthropic.RateLimitError``, ``google.api_core.exceptions.ResourceExhausted``,
``openai.APIStatusError``, whatever shape each SDK happens to use -- at the
adapter boundary and re-raise one of these instead. Everything above the
adapter (chat service, API routes, the eventual retry/fallback logic) only
ever sees :class:`ProviderError`, and only ever branches on ``kind``, never
on a provider name or a native exception type.
"""

from enum import Enum


class ProviderErrorKind(str, Enum):
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    CONTEXT_LENGTH = "context_length"
    CONTENT_FILTER = "content_filter"
    TIMEOUT = "timeout"
    SERVER_ERROR = "server_error"
    BAD_REQUEST = "bad_request"
    UNKNOWN = "unknown"
    # Not one of the assignment's required categories -- added because
    # Provider.embed() (see base.py) needs a way to say "this provider/model
    # doesn't do this at all" that's distinguishable from BAD_REQUEST (the
    # request itself was malformed). Calling .embed() on a chat-only
    # provider isn't a malformed request; it's a capability mismatch.
    UNSUPPORTED = "unsupported"


class ProviderError(Exception):
    """``kind`` is what application code branches on. ``provider`` is
    optional metadata (which adapter raised this) for logs and
    user-facing messages -- never something calling code should switch on;
    if you find yourself writing ``if error.provider == "anthropic"``,
    that logic belongs inside an adapter instead."""

    def __init__(self, kind: ProviderErrorKind, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.provider = provider

    def __repr__(self) -> str:
        provider = f", provider={self.provider!r}" if self.provider else ""
        return f"ProviderError(kind={self.kind!r}, message={self.message!r}{provider})"
