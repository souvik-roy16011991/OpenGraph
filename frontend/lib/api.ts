"use client";

import type {
  ApiKeyRow,
  BuildHistoryDetail,
  BuildHistoryRow,
  BuildJobStatus,
  ChatHistoryDetail,
  ChatHistoryRow,
  ConfigHistoryRow,
  CreateApiKeyResponse,
  DeployResponse,
  DomainPayload,
  GraphConfigPayload,
  GraphStats,
  GraphVizPayload,
  AuditListResponse,
  InstantiateTemplateResponse,
  KBTemplate,
  LLMModelsResponse,
  EmbeddingModelsResponse,
  MeResponse,
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
// Render's `fromService.property: host` yields a bare hostname — prepend
// https:// when the scheme is missing so the fetch URL is absolute.
const RAW_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "").replace(/\/$/, "");
const BASE = RAW_BASE && !/^https?:\/\//i.test(RAW_BASE)
  ? `https://${RAW_BASE}`
  : RAW_BASE;

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

/** Read the JWT issued by /api/v1/auth/{signup,login} from localStorage.
 *
 * Written by ``lib/auth.ts::setSession``. If absent, the Authorization header
 * is omitted and the backend returns 401 — middleware will have redirected
 * the user to /sign-in already in that case.
 */
function activeAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem("auth_token");
  } catch {
    return null;
  }
}

function withHeaders(headers: HeadersInit = {}): HeadersInit {
  const h = new Headers(headers);
  const wid = activeWorkspaceId();
  if (wid) h.set("X-Workspace-Id", wid);
  const token = activeAuthToken();
  if (token) h.set("Authorization", `Bearer ${token}`);
  return h;
}

// Multipart helper: same as withHeaders but never sets Content-Type so the
// browser can fill in the multipart boundary. Still sends Authorization +
// X-Workspace-Id so the upload endpoint is authenticated + scoped.
function withMultipartHeaders(headers: HeadersInit = {}): HeadersInit {
  return withHeaders(headers);
}

/** Backend returns 404 with detail "Workspace <uuid> not found." when the
 *  X-Workspace-Id header points at a workspace that doesn't exist OR belongs
 *  to a different user (deps.py returns the same 404 for both to avoid tenant
 *  enumeration). Either way, the persisted active id is stale — drop it and
 *  send the user to the workspace picker instead of spamming failed requests.
 */
function isStaleWorkspaceError(status: number, detail: unknown): boolean {
  return (
    status === 404 &&
    typeof detail === "string" &&
    /^Workspace\s.+\snot found\.?$/i.test(detail)
  );
}

/** Backend returns 400 "Missing X-Workspace-Id header." when a workspace-
 *  scoped request is made without a selected workspace. That's a page-guard
 *  oversight — the user just hasn't picked one yet. Redirect to /workspaces
 *  to let them, instead of rendering a broken page full of error toasts.
 */
function isMissingWorkspaceHeaderError(status: number, detail: unknown): boolean {
  return (
    status === 400 &&
    typeof detail === "string" &&
    /missing\s+x-workspace-id\s+header/i.test(detail)
  );
}

function clearActiveWorkspace(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem("kb-active-workspace");
  } catch { /* ignore */ }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
    const stale = isStaleWorkspaceError(res.status, detail);
    const missing = isMissingWorkspaceHeaderError(res.status, detail);
    if (stale || missing) {
      // Stale id is in localStorage: wipe it. Missing header just means
      // nothing is selected yet — no-op clear is harmless.
      if (stale) clearActiveWorkspace();
      // Don't bounce if we're already on a route that can handle a missing
      // workspace — those pages render the picker inline. Avoids redirect
      // loops on /workspaces itself.
      if (typeof window !== "undefined") {
        const here = window.location.pathname;
        if (here !== "/workspaces" && here !== "/sign-in" && here !== "/sign-up") {
          window.location.assign("/workspaces");
        }
      }
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json() as Promise<T>;
}

/** Wrap `fetch` so the browser's opaque "TypeError: Failed to fetch" becomes
 *  a message users can actually act on. Triggered by: server unreachable,
 *  DNS failure, CORS rejection, offline, request aborted. */
async function safeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (err) {
    if (err instanceof TypeError) {
      throw new Error("Can't reach the server right now — please check your internet connection and refresh the page.");
    }
    throw err;
  }
}

async function get<T>(path: string, query?: Record<string, string | number | undefined>): Promise<T> {
  const qs = query
    ? "?" + new URLSearchParams(
        Object.entries(query).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])
      ).toString()
    : "";
  const res = await safeFetch(`${BASE}${path}${qs}`, {
    cache: "no-store",
    headers: withHeaders(),
  });
  return handle<T>(res);
}

async function json<T>(path: string, method: "POST" | "PUT" | "DELETE" | "PATCH", body?: unknown): Promise<T> {
  const res = await safeFetch(`${BASE}${path}`, {
    method,
    headers: withHeaders({ "Content-Type": "application/json" }),
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
  const base = withHeaders({ "Content-Type": "application/json" });
  const h = new Headers(base);
  h.set("X-Workspace-Id", workspaceIdOverride);
  const res = await safeFetch(`${BASE}${path}`, {
    method,
    headers: h,
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  return handle<T>(res);
}

/** GET variant that overrides X-Workspace-Id — used by the Playground to
 *  read data from workspaces other than the sidebar's active one.
 */
async function getWithWorkspace<T>(
  path: string,
  workspaceIdOverride: string,
  query?: Record<string, string | number | undefined>,
): Promise<T> {
  const qs = query
    ? "?" + new URLSearchParams(
        Object.entries(query).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])
      ).toString()
    : "";
  const h = new Headers(withHeaders());
  h.set("X-Workspace-Id", workspaceIdOverride);
  const res = await safeFetch(`${BASE}${path}${qs}`, { cache: "no-store", headers: h });
  return handle<T>(res);
}

export const api = {
  // status
  health: () => get<{ status: string; service: string }>("/health"),
  stats: () => get<GraphStats>("/api/v1/graph/stats"),
  statsFor: (ws_id: string) => getWithWorkspace<GraphStats>("/api/v1/graph/stats", ws_id),

  // upload — accepts multiple `knowledge_files` + multiple `tool_files`
  uploadKB: async (form: FormData): Promise<UploadResponse> => {
    const res = await safeFetch(`${BASE}/api/v1/kb/upload`, {
      method: "POST",
      body: form,
      cache: "no-store",
      headers: withMultipartHeaders(), // don't set Content-Type on multipart
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

  // deploy — mints a scoped API key + flips the workspace into the
  // /api/v1/ext/* surface. Plaintext key is in the response once.
  deployWorkspace: (
    ws_id: string,
    body: { key_name?: string; rate_limit_rpm?: number } = {},
  ) => json<DeployResponse>(`/api/v1/workspaces/${ws_id}/deploy`, "POST", body),

  // developer API keys — powers /api-keys dashboard
  listApiKeys: () => get<ApiKeyRow[]>("/api/v1/keys"),
  createApiKey: (body: { name: string; workspace_id?: string | null; rate_limit_rpm?: number }) =>
    json<CreateApiKeyResponse>("/api/v1/keys", "POST", body),
  revokeApiKey: (id: string) => json<{ ok: true }>(`/api/v1/keys/${id}`, "DELETE"),

  // config
  getDomain: () => get<DomainPayload>("/api/v1/config/domain"),
  putDomain: (p: DomainPayload) => json<{ ok: boolean; path: string }>("/api/v1/config/domain", "PUT", p),
  getGraphCfg: () => get<GraphConfigPayload>("/api/v1/config/graph"),
  getGraphCfgFor: (ws_id: string) =>
    getWithWorkspace<GraphConfigPayload>("/api/v1/config/graph", ws_id),
  getDomainFor: (ws_id: string) =>
    getWithWorkspace<DomainPayload>("/api/v1/config/domain", ws_id),
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

  // Current user + audit trail
  me: () => get<MeResponse>("/api/v1/me"),
  updateMe: (body: { display_name?: string }) =>
    json<MeResponse>("/api/v1/me", "PATCH", body),
  myAudit: (opts?: { limit?: number; before_id?: number; workspace_id?: string; action?: string }) =>
    get<AuditListResponse>("/api/v1/me/audit", opts),
  myBillingTransactions: (opts?: { limit?: number; before_id?: number }) =>
    get<import("./schema").CreditTransactionListResponse>("/api/v1/me/billing/transactions", opts),

  // Templates (stock + user-custom; `id` is a slug for stock, UUID for custom)
  listTemplates: (opts?: { q?: string; category?: string }) =>
    get<{ templates: KBTemplate[] }>("/api/v1/templates", opts),
  getTemplate: (id: string) => get<KBTemplate>(`/api/v1/templates/${encodeURIComponent(id)}`),
  createTemplate: (body: import("./schema").TemplateCreateInput) =>
    json<KBTemplate>("/api/v1/templates", "POST", body),
  updateTemplate: (id: string, body: import("./schema").TemplateUpdateInput) =>
    json<KBTemplate>(`/api/v1/templates/${encodeURIComponent(id)}`, "PATCH", body),
  deleteTemplate: (id: string) =>
    json<{ ok: true; deleted_template_id: string }>(
      `/api/v1/templates/${encodeURIComponent(id)}`, "DELETE",
    ),
  instantiateTemplate: (id: string, body: { name?: string; description?: string } = {}) =>
    json<InstantiateTemplateResponse>(`/api/v1/templates/${encodeURIComponent(id)}/instantiate`, "POST", body),

  // LLM selection
  listModels: (refresh = false) =>
    get<LLMModelsResponse>("/api/v1/llm/models", refresh ? { refresh: "true" } : undefined),
  listEmbeddingModels: (refresh = false) =>
    get<EmbeddingModelsResponse>(
      "/api/v1/llm/embedding-models",
      refresh ? { refresh: "true" } : undefined,
    ),
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
