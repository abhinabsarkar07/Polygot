import { apiFetch, apiJson } from "./client";
import type { CollectionDetail, CollectionSummary, DocumentInfo } from "./types";

export async function createCollection(tenantId: string, name: string): Promise<CollectionSummary> {
  return apiJson<CollectionSummary>("/api/collections", tenantId, {
    method: "POST",
    body: JSON.stringify({ name }),
  });
}

export async function listCollections(tenantId: string): Promise<CollectionSummary[]> {
  return apiJson<CollectionSummary[]>("/api/collections", tenantId);
}

export async function getCollection(tenantId: string, collectionId: string): Promise<CollectionDetail> {
  return apiJson<CollectionDetail>(`/api/collections/${collectionId}`, tenantId);
}

/**
 * multipart/form-data upload -- the file plus ingestion-time controls
 * (chunk size/overlap, see app/schemas/collections.py::UploadDocumentQuery
 * for why these are form fields here and not query-time JSON like
 * top_k/similarity_threshold are on the chat request).
 */
export async function uploadDocument(
  tenantId: string,
  collectionId: string,
  file: File,
  chunkSize: number,
  overlap: number,
): Promise<DocumentInfo> {
  const form = new FormData();
  form.append("file", file);
  form.append("chunk_size", String(chunkSize));
  form.append("overlap", String(overlap));

  const response = await apiFetch(`/api/collections/${collectionId}/documents`, tenantId, {
    method: "POST",
    body: form,
  });
  return response.json() as Promise<DocumentInfo>;
}
