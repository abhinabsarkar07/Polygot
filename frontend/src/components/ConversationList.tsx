import type { ConversationSummary } from "../api/types";

interface Props {
  conversations: ConversationSummary[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
  loading: boolean;
}

export function ConversationList({ conversations, selectedId, onSelect, onCreate, loading }: Props) {
  return (
    <nav className="conversation-list">
      <button onClick={onCreate} disabled={loading}>
        + New Conversation
      </button>
      <ul>
        {conversations.map((c) => (
          <li key={c.id}>
            <button className={c.id === selectedId ? "selected" : ""} onClick={() => onSelect(c.id)}>
              {c.title ?? `Conversation ${c.id.slice(0, 8)}`}
            </button>
          </li>
        ))}
      </ul>
    </nav>
  );
}
