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
import { getCachedAccessToken, primeTokenCache, setCachedAccessToken } from "./auth-token";
import { getSupabaseBrowserClient } from "./supabase";

// If NEXT_PUBLIC_API_BASE is set (e.g. http://localhost:8000), hit the backend
// directly — avoids Next.js dev-proxy body-size limits on multipart uploads.
// Empty string = same-origin, relying on next.config.ts rewrites().
//
// Render's `fromService.property: host` returns a bare hostname (no scheme);
// normalise so fetches work whether the env has a scheme or not.
function normaliseBase(raw: string): string {
  const trimmed = raw.replace(/\/$/, "");
  if (!trimmed) return "";
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  return `https://${trimmed}`;
}
const BASE = normaliseBase(process.env.NEXT_PUBLIC_API_BASE ?? "");

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

/** Return the current Supabase access token at call time.
 *
 * Pulls from the in-memory cache populated by `AuthGate` and primed via
 * `onAuthStateChange`. The browser's `@supabase/ssr` client stores the
 * session in cookies (not localStorage), so we can't read it
 * synchronously from storage — the cache is the sync source of truth.
 */
function activeAuthToken(): string | null {
  if (typeof window === "undefined") return null;
  primeTokenCache();
  return getCachedAccessToken();
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

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail: unknown = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* ignore */ }
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

// Paths where a 401 must NOT redirect to /sign-in — the user is already
// on an auth page, and looping on them would clobber in-progress flows
// (OAuth callback, sign-in form submission).
function isAuthLocation(): boolean {
  if (typeof window === "undefined") return false;
  const p = window.location.pathname;
  return p.startsWith("/sign-in") || p.startsWith("/sign-up") || p.startsWith("/auth/");
}

/** SSO-style fetch: on 401, force a Supabase session refresh (which rotates
 *  the access token via the refresh token) and retry once with the fresh
 *  bearer. If the session is genuinely gone or refresh fails, redirect to
 *  /sign-in with a `return_to` — silently, without surfacing an
 *  "Authentication required" error to the user.
 *
 *  `buildInit` is a closure so the retry picks up the refreshed token from
 *  the module-level cache via `withHeaders()`.
 *
 *  Uses `refreshSession()` rather than `getSession()` so we FORCE a token
 *  rotation even when the client-side clock thinks the old token is still
 *  valid — which is the common case when the server has rejected it (e.g.
 *  clock skew, remote revocation, rotated JWT secret).
 */
async function apiFetch(url: string, buildInit: () => RequestInit): Promise<Response> {
  let res = await safeFetch(url, buildInit());
  if (res.status !== 401) return res;

  let refreshed = false;
  try {
    const supabase = getSupabaseBrowserClient();
    const { data, error } = await supabase.auth.refreshSession();
    if (!error) {
      const fresh = data.session?.access_token ?? null;
      setCachedAccessToken(fresh);
      refreshed = !!fresh;
    }
  } catch {
    /* fall through to redirect */
  }

  if (refreshed) {
    res = await safeFetch(url, buildInit());
    if (res.status !== 401) return res;
  }

  // Genuine auth failure — clear the cached token so stale headers aren't
  // re-sent and bounce to sign-in (unless we're already there, to prevent
  // a redirect loop).
  setCachedAccessToken(null);
  if (typeof window !== "undefined" && !isAuthLocation()) {
    const returnTo = window.location.pathname + window.location.search;
    window.location.replace(`/sign-in?return_to=${encodeURIComponent(returnTo)}`);
  }
  return res;
}

async function get<T>(path: string, query?: Record<string, string | number | undefined>): Promise<T> {
  const qs = query
    ? "?" + new URLSearchParams(
        Object.entries(query).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])
      ).toString()
    : "";
  const res = await apiFetch(`${BASE}${path}${qs}`, () => ({
    cache: "no-store",
    headers: withHeaders(),
  }));
  return handle<T>(res);
}

async function json<T>(path: string, method: "POST" | "PUT" | "DELETE" | "PATCH", body?: unknown): Promise<T> {
  const res = await apiFetch(`${BASE}${path}`, () => ({
    method,
    headers: withHeaders({ "Content-Type": "application/json" }),
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  }));
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
  const res = await apiFetch(`${BASE}${path}`, () => {
    const h = new Headers(withHeaders({ "Content-Type": "application/json" }));
    h.set("X-Workspace-Id", workspaceIdOverride);
    return {
      method,
      headers: h,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    };
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
  const res = await apiFetch(`${BASE}${path}${qs}`, () => {
    const h = new Headers(withHeaders());
    h.set("X-Workspace-Id", workspaceIdOverride);
    return { cache: "no-store", headers: h };
  });
  return handle<T>(res);
}

export const api = {
  // status
  health: () => get<{ status: string; service: string }>("/health"),
  stats: () => get<GraphStats>("/api/v1/graph/stats"),
  statsFor: (ws_id: string) => getWithWorkspace<GraphStats>("/api/v1/graph/stats", ws_id),

  // upload — accepts multiple `knowledge_files` + multiple `tool_files`
  uploadKB: async (form: FormData): Promise<UploadResponse> => {
    const res = await apiFetch(`${BASE}/api/v1/kb/upload`, () => ({
      method: "POST",
      body: form,
      cache: "no-store",
      headers: withMultipartHeaders(),
    }));
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

  // Templates
  listTemplates: (opts?: { q?: string; category?: string }) =>
    get<{ templates: KBTemplate[] }>("/api/v1/templates", opts),
  getTemplate: (slug: string) => get<KBTemplate>(`/api/v1/templates/${slug}`),
  instantiateTemplate: (slug: string, body: { name?: string; description?: string } = {}) =>
    json<InstantiateTemplateResponse>(`/api/v1/templates/${slug}/instantiate`, "POST", body),

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
