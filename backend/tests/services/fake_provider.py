"""A scripted Provider for ChatService tests -- yields a fixed sequence of
StreamEvents (or hangs, for cancellation tests), proving the orchestration
layer only depends on the Provider interface, never a concrete adapter.
"""

import asyncio
from collections.abc import AsyncIterator

from app.providers.base import Provider
from app.providers.contracts import CompletionRequest, CompletionResponse, StreamEvent

HANG = object()  # sentinel: sleep "forever" at this point in the script, for cancellation tests


class ScriptedProvider(Provider):
    def __init__(self, provider_id: str, events: list) -> None:
        self.id = provider_id
        self._events = events
        self.received_requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        raise NotImplementedError("ChatService in CP-04 only uses streaming")

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        self.received_requests.append(request)
        for event in self._events:
            if event is HANG:
                await asyncio.sleep(3600)
            else:
                yield event
