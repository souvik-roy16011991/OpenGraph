"use client";

import type {
  BuildJobStatus,
  DomainPayload,
  GraphConfigPayload,
  GraphStats,
  GraphVizPayload,
  NodeDetail,
  PutGraphConfigResponse,
  QueryRequest,
  QueryResponse,
  SearchResponse,
  StartBuildRequest,
  UploadResponse,
} from "./schema";

// If NEXT_PUBLIC_API_BASE is set (e.g. http://localhost:8000), hit the backend
// directly — avoids Next.js dev-proxy body-size limits on multipart uploads.
// Empty string = same-origin, relying on next.config.ts rewrites().
const BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "").replace(/\/$/, "");

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

async function get<T>(path: string, query?: Record<string, string | number | undefined>): Promise<T> {
  const qs = query
    ? "?" + new URLSearchParams(
        Object.entries(query).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])
      ).toString()
    : "";
  const res = await fetch(`${BASE}${path}${qs}`, { cache: "no-store" });
  return handle<T>(res);
}

async function json<T>(path: string, method: "POST" | "PUT" | "DELETE", body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  return handle<T>(res);
}

export const api = {
  // status
  health: () => get<{ status: string; service: string }>("/health"),
  stats: () => get<GraphStats>("/api/v1/graph/stats"),

  // upload
  uploadKB: async (form: FormData): Promise<UploadResponse> => {
    const res = await fetch(`${BASE}/api/v1/kb/upload`, { method: "POST", body: form, cache: "no-store" });
    return handle<UploadResponse>(res);
  },

  // config
  getDomain: () => get<DomainPayload>("/api/v1/config/domain"),
  putDomain: (p: DomainPayload) => json<{ ok: boolean; path: string }>("/api/v1/config/domain", "PUT", p),
  getGraphCfg: () => get<GraphConfigPayload>("/api/v1/config/graph"),
  putGraphCfg: (p: GraphConfigPayload) => json<PutGraphConfigResponse>("/api/v1/config/graph", "PUT", p),

  // build
  startBuild: (r: StartBuildRequest) => json<{ job_id: string; status: string }>("/api/v1/build", "POST", r),
  buildStatus: (job_id: string) => get<BuildJobStatus>(`/api/v1/build/${job_id}`),
  currentBuild: () => get<{ running: boolean; job_id: string | null; status?: string | null }>("/api/v1/build"),

  // graph data
  visualization: (opts?: { max_nodes?: number; kb_source?: string; node_types?: string; edge_types?: string }) =>
    get<GraphVizPayload>("/api/v1/graph/visualization", opts),
  getNode: (id: string) => get<NodeDetail>(`/api/v1/graph/node/${encodeURIComponent(id)}`),
  search: (q: string, opts?: { top_k?: number; node_type?: string }) =>
    get<SearchResponse>("/api/v1/graph/search", { q, ...opts }),
  tree: (max_depth = 2) => get<{ tree: unknown[] }>("/api/v1/graph/tree", { max_depth }),

  // agent
  query: (r: QueryRequest) => json<QueryResponse>("/api/v1/query", "POST", r),
};
