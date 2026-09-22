import { useState } from "react";
import type { CitedSource } from "../api/types";

export interface DisplayMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  status: "complete" | "interrupted" | "streaming";
  modelId?: string | null;
  sources?: CitedSource[];
  // Set only when the primary model failed before any output and a
  // configured fallback model (app/providers/models.yaml) took over --
  // see app/services/chat.py's FallbackEvent.
  fallbackFrom?: string;
}

interface Props {
  messages: DisplayMessage[];
}

function SourceList({ sources }: { sources: CitedSource[] }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  if (sources.length === 0) return null;

  return (
    <div className="source-list">
      <div className="source-list-label">Sources:</div>
      {sources.map((s) => (
        <div key={s.id} className="source-item">
          <button className="source-chip" onClick={() => setExpandedId(expandedId === s.id ? null : s.id)}>
            [{s.id}] {s.filename}
            {s.page_number !== null ? `, p.${s.page_number}` : ""} (chunk {s.chunk_index}, similarity {s.similarity.toFixed(2)})
          </button>
          {expandedId === s.id && <div className="source-detail">{s.text}</div>}
        </div>
      ))}
    </div>
  );
}

export function MessageList({ messages }: Props) {
  return (
    <div className="message-list">
      {messages.map((m) => (
        <div key={m.id} className={`message message-${m.role}`}>
          <div className="message-meta">
            {m.role}
            {m.modelId ? ` · ${m.modelId}` : ""}
            {m.status === "interrupted" && " · stopped"}
            {m.fallbackFrom && (
              <span className="fallback-badge" title={`${m.fallbackFrom} was unavailable; retried with ${m.modelId}`}>
                {" "}
                · fallback from {m.fallbackFrom}
              </span>
            )}
          </div>
          <div className="message-text">
            {m.text}
            {m.status === "streaming" && <span className="cursor">▍</span>}
          </div>
          {m.sources && <SourceList sources={m.sources} />}
        </div>
      ))}
    </div>
  );
}
