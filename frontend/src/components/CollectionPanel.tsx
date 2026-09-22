import { useEffect, useRef, useState } from "react";
import { createCollection, getCollection, listCollections, uploadDocument } from "../api/collections";
import { ApiError } from "../api/client";
import type { CollectionDetail, CollectionSummary } from "../api/types";

interface Props {
  tenantId: string;
  selectedCollectionId: string | null;
  onSelectCollection: (id: string | null) => void;
}

export function CollectionPanel({ tenantId, selectedCollectionId, onSelectCollection }: Props) {
  const [collections, setCollections] = useState<CollectionSummary[]>([]);
  const [detail, setDetail] = useState<CollectionDetail | null>(null);
  const [newName, setNewName] = useState("");
  const [chunkSize, setChunkSize] = useState(1000);
  const [overlap, setOverlap] = useState(150);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listCollections(tenantId)
      .then(setCollections)
      .catch(() => setCollections([]));
  }, [tenantId]);

  useEffect(() => {
    if (!selectedCollectionId) {
      setDetail(null);
      return;
    }
    refreshDetail(selectedCollectionId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId, selectedCollectionId]);

  async function refreshDetail(collectionId: string) {
    try {
      setDetail(await getCollection(tenantId, collectionId));
    } catch {
      setDetail(null);
    }
  }

  async function handleCreate() {
    const name = newName.trim();
    if (!name) return;
    const created = await createCollection(tenantId, name);
    setNewName("");
    setCollections((prev) => [created, ...prev]);
    onSelectCollection(created.id);
  }

  async function handleUpload() {
    const file = fileInputRef.current?.files?.[0];
    if (!file || !selectedCollectionId) return;
    setUploading(true);
    setError(null);
    try {
      await uploadDocument(tenantId, selectedCollectionId, file, chunkSize, overlap);
      await refreshDetail(selectedCollectionId);
      if (fileInputRef.current) fileInputRef.current.value = "";
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="collection-panel">
      <h3>Documents (RAG)</h3>

      <select value={selectedCollectionId ?? ""} onChange={(e) => onSelectCollection(e.target.value || null)}>
        <option value="">No collection (plain chat)</option>
        {collections.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>

      <div className="collection-create-row">
        <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="New collection name" />
        <button onClick={handleCreate} disabled={!newName.trim()}>
          Create
        </button>
      </div>

      {selectedCollectionId && (
        <div className="collection-upload">
          {/* Ingestion-time controls (STEP 25) -- fixed for a document once
              uploaded; changing these later means re-uploading, not a
              query-time toggle. Query-time controls (top-k, similarity
              threshold) live in the chat composer instead, see Chat.tsx. */}
          <div className="chunking-controls">
            <label>
              Chunk size{" "}
              <input type="number" value={chunkSize} min={100} max={8000} onChange={(e) => setChunkSize(Number(e.target.value))} />
            </label>
            <label>
              Overlap <input type="number" value={overlap} min={0} onChange={(e) => setOverlap(Number(e.target.value))} />
            </label>
          </div>
          <input type="file" ref={fileInputRef} accept=".txt,.md,.markdown,.pdf" />
          <button onClick={handleUpload} disabled={uploading}>
            {uploading ? "Uploading..." : "Upload"}
          </button>
          {error && <div className="error-banner">{error}</div>}

          <ul className="document-list">
            {detail?.documents.map((d) => (
              <li key={d.id}>
                {d.filename} -- <span className={`doc-status doc-status-${d.status}`}>{d.status}</span>
                {d.status === "failed" && d.error && <span className="doc-error"> ({d.error})</span>}
              </li>
            ))}
            {detail?.documents.length === 0 && <li className="document-list-empty">No documents yet.</li>}
          </ul>
        </div>
      )}
    </div>
  );
}
