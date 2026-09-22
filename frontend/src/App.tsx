import { useEffect, useState } from "react";
import { createConversation, listConversations } from "./api/conversations";
import { fetchHealth } from "./api/health";
import { ConversationList } from "./components/ConversationList";
import { Chat } from "./components/Chat";
import type { ConversationSummary } from "./api/types";

type BackendStatus = "checking" | "connected" | "unavailable";

const DEFAULT_TENANT = "tenant-a";

function App() {
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("checking");
  const [tenantId, setTenantId] = useState(DEFAULT_TENANT);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loadingConversations, setLoadingConversations] = useState(false);

  useEffect(() => {
    fetchHealth()
      .then(() => setBackendStatus("connected"))
      .catch(() => setBackendStatus("unavailable"));
  }, []);

  useEffect(() => {
    setSelectedId(null);
    setLoadingConversations(true);
    listConversations(tenantId)
      .then((fetched) => {
        setConversations(fetched);
        setSelectedId(fetched[0]?.id ?? null);
      })
      .catch(() => setConversations([]))
      .finally(() => setLoadingConversations(false));
  }, [tenantId]);

  async function handleCreateConversation() {
    setLoadingConversations(true);
    try {
      const created = await createConversation(tenantId);
      setConversations((prev) => [created, ...prev]);
      setSelectedId(created.id);
    } finally {
      setLoadingConversations(false);
    }
  }

  return (
    <div className="app">
      <header>
        <h1>Polyglot</h1>
        <p>Multi-Provider AI Workbench &mdash; Backend: {backendStatus}</p>
        <label>
          Tenant:{" "}
          <input value={tenantId} onChange={(e) => setTenantId(e.target.value)} placeholder="tenant-a" />
        </label>
      </header>
      <div className="layout">
        <ConversationList
          conversations={conversations}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onCreate={handleCreateConversation}
          loading={loadingConversations}
        />
        {selectedId ? (
          <Chat key={selectedId} tenantId={tenantId} conversationId={selectedId} />
        ) : (
          <section className="chat chat-empty">
            <p>Create a conversation to get started.</p>
          </section>
        )}
      </div>
    </div>
  );
}

export default App;
