export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function parseErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // response wasn't JSON -- fall through to the generic message below
  }
  return `Request failed with status ${response.status}`;
}

/** Every request carries the tenant identity as the unsigned `X-Tenant-Id`
 * header CP-01 established -- never a body field, never a query param. */
export async function apiFetch(path: string, tenantId: string, init: RequestInit = {}): Promise<Response> {
  // A FormData body (multipart upload -- see api/collections.ts) must
  // NOT get an explicit Content-Type: the browser sets
  // "multipart/form-data; boundary=..." itself, with a boundary value
  // only it knows: an explicit "application/json" here would silently
  // break every upload's request body.
  const isFormData = typeof FormData !== "undefined" && init.body instanceof FormData;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: {
      "X-Tenant-Id": tenantId,
      ...(init.body && !isFormData ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorDetail(response));
  }
  return response;
}

export async function apiJson<T>(path: string, tenantId: string, init: RequestInit = {}): Promise<T> {
  const response = await apiFetch(path, tenantId, init);
  return response.json() as Promise<T>;
}
