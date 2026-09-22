export interface DisplayMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  status: "complete" | "interrupted" | "streaming";
  modelId?: string | null;
}

interface Props {
  messages: DisplayMessage[];
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
          </div>
          <div className="message-text">
            {m.text}
            {m.status === "streaming" && <span className="cursor">▍</span>}
          </div>
        </div>
      ))}
    </div>
  );
}
