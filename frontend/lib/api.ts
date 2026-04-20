"use client";

import type {
  BuildHistoryDetail,
  BuildHistoryRow,
  BuildJobStatus,
  ChatHistoryDetail,
  ChatHistoryRow,
  ConfigHistoryRow,
  DomainPayload,
  GraphConfigPayload,
  GraphStats,
  GraphVizPayload,
  InstantiateTemplateResponse,
  KBTemplate,
  LLMModelsResponse,
  NodeDetail,
  PutGraphConfigResponse,
  QueryRequest,
  QueryResponse,
  SearchResponse,
  StartBuildRequest,
  UploadHistoryRow,
  UploadResponse,
  WorkspaceFileInfo,
  WorkspaceLLMResponse,
  WorkspaceSummary,
} from "./schema";

// If NEXT_PUBLIC_API_BASE is set (e.g. http://localhost:8000), hit the backend
// directly — avoids Next.js dev-proxy body-size limits on multipart uploads.
// Empty string = same-origin, relying on next.config.ts rewrites().
const BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "").replace(/\/$/, "");

/** Read the active workspace id from the persisted zustand store at call time. */
function activeWorkspaceId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    // Dynamic require to avoid SSR import of zustand's persist middleware.
    const raw = window.localStorage.getItem("kb-active-workspace");
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { state?: { activeId?: string | null } };
    return parsed?.state?.activeId ?? null;
  } catch {
    return null;
  }
}

/** Best-effort read of the Stack Auth access token from the client SDK cookie.
 *
 * Stack's nextjs-cookie token store persists auth state under a well-known
 * cookie name; the client SDK exposes it via the useUser() hook, but the
 * typical access-token is also readable synchronously via the Stack client
 * app. We keep this read optional so a page without Stack configured still
 * works — when no token is available, the Authorization header is just
 * omitted and the backend falls back to the anonymous path (Phase 1b).
 */
async function activeAuthToken(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  try {
    // Lazy-import to avoid pulling the Stack SDK into server bundles that
    // don't have it configured.
    const mod = await import("@stackframe/stack");
    const clientApp = (mod as unknown as {
      StackClientApp?: new (opts: {
        tokenStore: string;
        projectId: string;
        publishableClientKey: string;
      }) => {
        getUser: () => Promise<{ getAuthJson: () => Promise<{ accessToken?: string }> } | null>;
      };
    }).StackClientApp;
    const projectId = process.env.NEXT_PUBLIC_STACK_PROJECT_ID;
    const publishableClientKey = process.env.NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY;
    if (!clientApp || !projectId || !publishableClientKey) return null;
    const app = new clientApp({ tokenStore: "nextjs-cookie", projectId, publishableClientKey });
    const user = await app.getUser();
    if (!user) return null;
    const auth = await user.getAuthJson();
    return auth.accessToken ?? null;
  } catch {
    return null;
  }
}

async function withHeaders(headers: HeadersInit = {}): Promise<HeadersInit> {
  const h = new Headers(headers);
  const wid = activeWorkspaceId();
  if (wid) h.set("X-Workspace-Id", wid);
  const token = await activeAuthToken();
  if (token) h.set("Authorization", `Bearer ${token}`);
  return h;
}

// Legacy name kept for any callers still using the old helper synchronously
// (multipart upload path). Synchronous — no Authorization header in this
// code path; the backend tolerates that today. Once Phase 1d enforces auth,
// the upload path will be migrated to the async helper.
function withWorkspaceHeader(headers: HeadersInit = {}): HeadersInit {
  const wid = activeWorkspaceId();
  if (!wid) return headers;
  const h = new Headers(headers);
  h.set("X-Workspace-Id", wid);
  return h;
}

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
  const res = await fetch(`${BASE}${path}${qs}`, {
    cache: "no-store",
    headers: await withHeaders(),
  });
  return handle<T>(res);
}

async function json<T>(path: string, method: "POST" | "PUT" | "DELETE" | "PATCH", body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: await withHeaders({ "Content-Type": "application/json" }),
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  return handle<T>(res);
}

/** POST/GET variant that overrides X-Workspace-Id — used by the standalone
 *  chat page where the active chat workspace is independent of the sidebar's.
 */
async function jsonWithWorkspace<T>(
  path: string,
  method: "POST" | "PUT" | "DELETE" | "PATCH",
  body: unknown,
  workspaceIdOverride: string,
): Promise<T> {
  const base = await withHeaders({ "Content-Type": "application/json" });
  const h = new Headers(base);
  h.set("X-Workspace-Id", workspaceIdOverride);
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: h,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  return handle<T>(res);
}

export const api = {
  // status
  health: () => get<{ status: string; service: string }>("/health"),
  stats: () => get<GraphStats>("/api/v1/graph/stats"),

  // upload — accepts multiple `knowledge_files` + multiple `tool_files`
  uploadKB: async (form: FormData): Promise<UploadResponse> => {
    const res = await fetch(`${BASE}/api/v1/kb/upload`, {
      method: "POST",
      body: form,
      cache: "no-store",
      headers: withWorkspaceHeader(), // don't set Content-Type on multipart
    });
    return handle<UploadResponse>(res);
  },

  // workspaces
  listWorkspaces: () => get<{ workspaces: WorkspaceSummary[] }>("/api/v1/workspaces"),
  createWorkspace: (body: { name: string; description?: string }) =>
    json<WorkspaceSummary>("/api/v1/workspaces", "POST", body),
  getWorkspace: (id: string) => get<WorkspaceSummary>(`/api/v1/workspaces/${id}`),
  updateWorkspace: (id: string, body: { name?: string; description?: string }) =>
    json<WorkspaceSummary>(`/api/v1/workspaces/${id}`, "PATCH", body),
  deleteWorkspace: (id: string) => json<{ ok: true }>(`/api/v1/workspaces/${id}`, "DELETE"),
  listWorkspaceFiles: (id: string, kb_source?: "knowledge" | "tool") =>
    get<{ files: WorkspaceFileInfo[] }>(`/api/v1/workspaces/${id}/files`, { kb_source }),
  deleteWorkspaceFile: (ws_id: string, file_id: number) =>
    json<{ ok: true }>(`/api/v1/workspaces/${ws_id}/files/${file_id}`, "DELETE"),

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
  query: (r: QueryRequest, workspaceIdOverride?: string) =>
    workspaceIdOverride
      ? jsonWithWorkspace<QueryResponse>("/api/v1/query", "POST", r, workspaceIdOverride)
      : json<QueryResponse>("/api/v1/query", "POST", r),

  // Templates
  listTemplates: (opts?: { q?: string; category?: string }) =>
    get<{ templates: KBTemplate[] }>("/api/v1/templates", opts),
  getTemplate: (slug: string) => get<KBTemplate>(`/api/v1/templates/${slug}`),
  instantiateTemplate: (slug: string, body: { name?: string; description?: string } = {}) =>
    json<InstantiateTemplateResponse>(`/api/v1/templates/${slug}/instantiate`, "POST", body),

  // LLM selection
  listModels: (refresh = false) =>
    get<LLMModelsResponse>("/api/v1/llm/models", refresh ? { refresh: "true" } : undefined),
  getWorkspaceLLM: (ws_id: string) =>
    get<WorkspaceLLMResponse>(`/api/v1/workspaces/${ws_id}/llm`),
  setWorkspaceLLM: (ws_id: string, model: string | null) =>
    json<WorkspaceLLMResponse>(`/api/v1/workspaces/${ws_id}/llm`, "PUT", { model }),

  // history (Neon-backed)
  historyBuilds: (limit = 50) =>
    get<{ builds: BuildHistoryRow[] }>("/api/v1/history/builds", { limit }),
  historyBuildDetail: (job_id: string) =>
    get<BuildHistoryDetail>(`/api/v1/history/builds/${encodeURIComponent(job_id)}`),
  historyChats: (limit = 50) =>
    get<{ chats: ChatHistoryRow[] }>("/api/v1/history/chats", { limit }),
  historyChatDetail: (session_id: string) =>
    get<ChatHistoryDetail>(`/api/v1/history/chats/${encodeURIComponent(session_id)}`),
  historyConfigs: (kind?: "domain" | "graph", limit = 50) =>
    get<{ configs: ConfigHistoryRow[] }>("/api/v1/history/configs", { kind, limit }),
  historyUploads: (kb_source?: "knowledge" | "tool", limit = 50) =>
    get<{ uploads: UploadHistoryRow[] }>("/api/v1/history/uploads", { kb_source, limit }),
};
