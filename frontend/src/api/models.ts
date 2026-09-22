import { apiJson } from "./client";
import type { ModelSummary } from "./types";

export async function listModels(tenantId: string): Promise<ModelSummary[]> {
  return apiJson<ModelSummary[]>("/api/models", tenantId);
}
