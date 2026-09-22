"""A fake Provider used only to prove the architecture is extensible --
see test_extensibility.py. Implementing this against nothing but
``app.providers.base.Provider`` is the whole point: if a fake, throwaway
provider can satisfy the interface and work with generic resolution code,
a real fourth provider (DeepSeek, whoever) can too, without touching
anything above the adapter layer.
"""

from collections.abc import AsyncIterator

from app.providers import (
    CompletionRequest,
    CompletionResponse,
    DoneEvent,
    FinishReason,
    Provider,
    StreamEvent,
    TextBlock,
    TextDeltaEvent,
    Usage,
    UsageEvent,
)


class FakeProvider(Provider):
    id = "fake"

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        reply = f"echo: {request.messages[-1].content[0].text}"
        return CompletionResponse(
            model=request.model,
            content=[TextBlock(text=reply)],
            usage=Usage(input_tokens=5, output_tokens=len(reply.split())),
            finish_reason=FinishReason.STOP,
        )

    async def stream(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        reply = f"echo: {request.messages[-1].content[0].text}"
        for word in reply.split(" "):
            yield TextDeltaEvent(text=word + " ")
        yield UsageEvent(usage=Usage(input_tokens=5, output_tokens=len(reply.split())))
        yield DoneEvent(finish_reason=FinishReason.STOP)
