import { useEffect, useRef, useState } from "react";
import { getConversation, streamMessage } from "../api/conversations";
import { ApiError } from "../api/client";
import { ModelSelector } from "./ModelSelector";
import { MessageList, type DisplayMessage } from "./MessageList";

interface Props {
  tenantId: string;
  conversationId: string;
}

function toDisplayMessages(detail: Awaited<ReturnType<typeof getConversation>>): DisplayMessage[] {
  return detail.messages.map((m) => ({
    id: m.id,
    role: m.role === "user" ? "user" : "assistant",
    text: m.content.map((block) => (block.type === "text" ? block.text : "")).join(""),
    status: m.status,
    modelId: m.model_id,
  }));
}

export function Chat({ tenantId, conversationId }: Props) {
  const [messages, setMessages] = useState<DisplayMessage[]>([]);
  const [input, setInput] = useState("");
  const [model, setModel] = useState("");
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setMessages([]);
    setError(null);
    getConversation(tenantId, conversationId)
      .then((detail) => setMessages(toDisplayMessages(detail)))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [tenantId, conversationId]);

  useEffect(() => {
    // Stop takes user's Stop button; unmounting/switching conversations
    // while a generation is in flight must abort it too, not leave it
    // running invisibly in the background.
    return () => abortRef.current?.abort();
  }, [conversationId]);

  async function handleSend() {
    const content = input.trim();
    if (!content || generating || !model) return;

    setError(null);
    setInput("");
    setGenerating(true);

    const userMessage: DisplayMessage = { id: `local-user-${Date.now()}`, role: "user", text: content, status: "complete" };
    const assistantId = `local-assistant-${Date.now()}`;
    const assistantMessage: DisplayMessage = { id: assistantId, role: "assistant", text: "", status: "streaming", modelId: model };
    setMessages((prev) => [...prev, userMessage, assistantMessage]);

    const controller = new AbortController();
    abortRef.current = controller;

    function updateAssistant(patch: Partial<DisplayMessage>) {
      setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, ...patch } : m)));
    }

    try {
      for await (const event of streamMessage(tenantId, conversationId, content, model, controller.signal)) {
        if (event.type === "text_delta") {
          setMessages((prev) => prev.map((m) => (m.id === assistantId ? { ...m, text: m.text + event.text } : m)));
        } else if (event.type === "done") {
          updateAssistant({ status: "complete" });
        } else if (event.type === "error") {
          setError(event.message);
          updateAssistant({ status: "interrupted" });
        }
      }
    } catch (err) {
      if (controller.signal.aborted) {
        updateAssistant({ status: "interrupted" });
      } else {
        setError(err instanceof ApiError ? err.message : "Connection to the backend was lost.");
        updateAssistant({ status: "interrupted" });
      }
    } finally {
      setGenerating(false);
      abortRef.current = null;
    }
  }

  function handleStop() {
    abortRef.current?.abort();
  }

  return (
    <section className="chat">
      <MessageList messages={messages} />
      {error && <div className="error-banner">{error}</div>}
      <div className="composer">
        <ModelSelector tenantId={tenantId} value={model} onChange={setModel} disabled={generating} />
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              handleSend();
            }
          }}
          placeholder="Send a message..."
          disabled={generating}
        />
        {generating ? (
          <button onClick={handleStop}>Stop</button>
        ) : (
          <button onClick={handleSend} disabled={!input.trim() || !model}>
            Send
          </button>
        )}
      </div>
    </section>
  );
}
