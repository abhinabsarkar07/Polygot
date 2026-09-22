import { apiFetch, apiJson } from "./client";
import { parseSseStream } from "./sse";
import type { ConversationDetail, ConversationSummary, StreamEvent } from "./types";

export async function createConversation(tenantId: string, title?: string): Promise<ConversationSummary> {
  return apiJson<ConversationSummary>("/api/conversations", tenantId, {
    method: "POST",
    body: JSON.stringify({ title: title ?? null }),
  });
}

export async function listConversations(tenantId: string): Promise<ConversationSummary[]> {
  return apiJson<ConversationSummary[]>("/api/conversations", tenantId);
}

export async function getConversation(tenantId: string, conversationId: string): Promise<ConversationDetail> {
  return apiJson<ConversationDetail>(`/api/conversations/${conversationId}`, tenantId);
}

/**
 * Streams one assistant reply. Consumes the parsed SSE records and yields
 * typed, provider-neutral StreamEvent objects -- the caller never sees SSE
 * framing or provider-shaped data, only the same contract the backend's
 * adapters normalize into (see app/providers/contracts.py).
 *
 * `signal` is the caller's AbortController.signal: aborting it tears down
 * the underlying fetch, which the backend's disconnect detection turns
 * into real upstream cancellation (see docs/DESIGN.md, "Cancellation").
 */
export async function* streamMessage(
  tenantId: string,
  conversationId: string,
  content: string,
  model: string,
  signal: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await apiFetch(`/api/conversations/${conversationId}/messages/stream`, tenantId, {
    method: "POST",
    body: JSON.stringify({ content, model }),
    signal,
  });
  if (!response.body) return;

  for await (const record of parseSseStream(response.body)) {
    // The backend's JSON payload already carries its own `type` field
    // matching the SSE event name (app/api/conversations.py::_format_sse
    // uses the event's own discriminator as the SSE event name) -- no
    // separate mapping needed here.
    yield JSON.parse(record.data) as StreamEvent;
  }
}
