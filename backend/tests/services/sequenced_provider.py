"""A fake Provider whose `.stream()` behaves differently on successive
calls -- needed for CP-06 retry/fallback tests, where "the second attempt
succeeds after the first fails" is the entire point. `ScriptedProvider`
(fake_provider.py, CP-04) always does the same thing every call, which
can't express that.
"""

import asyncio
from collections.abc import AsyncIterator

from app.providers.base import Provider
from app.providers.contracts import CompletionRequest, CompletionResponse, StreamEvent

HANG = object()  # sleep "forever" at this point -- for timeout tests


class SequencedProvider(Provider):
    def __init__(self, provider_id: str, attempts: list[list]) -> None:
        self.id = provider_id
        self._attempts = attempts
        self.call_count = 0
        self.received_requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise NotImplementedError

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        self.received_requests.append(request)
        index = min(self.call_count, len(self._attempts) - 1)
        self.call_count += 1
        for event in self._attempts[index]:
            if event is HANG:
                await asyncio.sleep(3600)
            else:
                yield event
