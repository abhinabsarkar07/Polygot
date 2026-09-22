// Mirrors app/providers/messages.py's ContentBlock union and
// app/providers/contracts.py's StreamEvent union. The frontend only ever
// renders `text` content in CP-04 (no tool UI yet), but the type stays
// honest about what the backend can actually send.

export type ContentBlock =
  | { type: "text"; text: string }
  | { type: "image"; media_type: string; data: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error: boolean };

export interface ModelCapabilities {
  streaming: boolean;
  tools: boolean;
  vision: boolean;
  embeddings: boolean;
  reasoning: boolean;
}

export interface ModelSummary {
  id: string;
  provider: string;
  context_window: number;
  capabilities: ModelCapabilities;
}

export interface ConversationSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessageResponse {
  id: string;
  role: "user" | "assistant" | "tool";
  content: ContentBlock[];
  model_id: string | null;
  status: "complete" | "interrupted";
  created_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: MessageResponse[];
}

export type StreamEvent =
  | { type: "text_delta"; text: string }
  | { type: "tool_use_start"; id: string; name: string }
  | { type: "tool_use_delta"; id: string; partial_json: string }
  | { type: "tool_use_complete"; id: string; name: string; input: Record<string, unknown> }
  | { type: "usage"; usage: Record<string, number | null> }
  | { type: "done"; finish_reason: string }
  | { type: "error"; kind: string; message: string };
