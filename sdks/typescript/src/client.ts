/**
 * OpenGraphClient — single class covering the public /api/v1/ext/* surface.
 *
 * Works in any runtime that exposes `fetch` (Node ≥ 18 and modern browsers
 * both qualify). Zero runtime dependencies; types come from `./types`,
 * errors from `./errors`.
 *
 * The client groups related endpoints under `graph` and `history` to keep
 * call sites readable:
 *
 *    await client.query({ workspaceId, query: "..." })
 *    await client.graph.stats({ workspaceId })
 *    await client.history.chats({ workspaceId })
 *
 * Retries: the `_request` helper replays 429 / 502 / 503 / 504 / network
 * errors with exponential backoff (and honours `Retry-After`). 4xx errors
 * other than 429 throw immediately.
 */

import {
  AuthError,
  NotFoundError,
  OpenGraphError,
  RateLimitError,
  ServerError,
  ValidationError,
} from "./errors";
import type {
  BuildJobSummary,
  ChatSessionDetail,
  ChatSessionSummary,
  GraphSearchRequest,
  GraphStats,
  NodeDetail,
  QueryRequest,
  QueryResponse,
  SearchResponse,
  TraverseRequestArgs,
  TraverseResponse,
} from "./types";

const DEFAULT_BASE_URL = "https://api.opengraph.example";
const DEFAULT_TIMEOUT_MS = 60_000;
const USER_AGENT = "opengraph-sdk-ts/0.1.0";
const RETRYABLE_STATUSES = new Set([429, 502, 503, 504]);
const DEFAULT_RETRIES = 2;

export interface OpenGraphClientOptions {
  apiKey?: string;
  baseUrl?: string;
  /** Default workspace id; individual calls can still override. */
  workspaceId?: string;
  timeoutMs?: number;
  maxRetries?: number;
  /** Injectable for tests; defaults to the global `fetch`. */
  fetch?: typeof fetch;
}

interface RequestInit {
  method: "GET" | "POST" | "DELETE";
  path: string;
  query?: Record<string, string | number | boolean | undefined | null>;
  body?: unknown;
}

function envVar(name: string): string | undefined {
  if (typeof process !== "undefined" && process.env) return process.env[name];
  return undefined;
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function backoffMs(attempt: number, retryAfter?: number): number {
  if (retryAfter && retryAfter > 0) return Math.max(1000, retryAfter * 1000);
  const base = Math.min(30_000, 500 * 2 ** attempt);
  return base + Math.random() * 0.25 * base;
}

function parseRetryAfter(h: Headers): number | undefined {
  const ra = h.get("retry-after");
  if (!ra) return undefined;
  const n = parseInt(ra, 10);
  return Number.isFinite(n) ? n : undefined;
}

function buildUrl(
  baseUrl: string,
  path: string,
  query?: Record<string, string | number | boolean | undefined | null>,
): string {
  const url = new URL(path, baseUrl + "/");
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      if (v === undefined || v === null) continue;
      url.searchParams.set(k, String(v));
    }
  }
  return url.toString();
}

async function throwForStatus(resp: Response): Promise<never> {
  let payload: unknown = undefined;
  try {
    payload = await resp.json();
  } catch {
    // Body might be HTML / empty — that's fine.
  }
  let detail: string | undefined;
  if (payload && typeof payload === "object" && "detail" in payload) {
    const d = (payload as { detail?: unknown }).detail;
    if (typeof d === "string") detail = d;
    else if (d && typeof d === "object" && "message" in d) {
      const m = (d as { message?: unknown }).message;
      if (typeof m === "string") detail = m;
    }
  }
  const message = detail ?? `HTTP ${resp.status}`;
  const opts = { status: resp.status, detail, payload };

  if (resp.status === 401) throw new AuthError(message, opts);
  if (resp.status === 404) throw new NotFoundError(message, opts);
  if (resp.status === 400 || resp.status === 422) throw new ValidationError(message, opts);
  if (resp.status === 429) {
    throw new RateLimitError(message, { ...opts, retryAfterSeconds: parseRetryAfter(resp.headers) });
  }
  if (resp.status >= 500) throw new ServerError(message, opts);
  throw new OpenGraphError(message, opts);
}

export class OpenGraphClient {
  readonly graph: GraphNamespace;
  readonly history: HistoryNamespace;

  private readonly apiKey: string;
  private readonly baseUrl: string;
  private readonly defaultWorkspaceId?: string;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;
  private readonly fetchImpl: typeof fetch;

  constructor(opts: OpenGraphClientOptions = {}) {
    this.apiKey = opts.apiKey ?? envVar("OPENGRAPH_API_KEY") ?? "";
    if (!this.apiKey) {
      throw new OpenGraphError(
        "Missing API key. Pass { apiKey } or set OPENGRAPH_API_KEY.",
      );
    }
    this.baseUrl = (opts.baseUrl ?? envVar("OPENGRAPH_BASE_URL") ?? DEFAULT_BASE_URL).replace(/\/+$/, "");
    this.defaultWorkspaceId = opts.workspaceId ?? envVar("OPENGRAPH_WORKSPACE_ID");
    this.timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.maxRetries = opts.maxRetries ?? DEFAULT_RETRIES;
    this.fetchImpl = opts.fetch ?? globalThis.fetch;
    if (!this.fetchImpl) {
      throw new OpenGraphError(
        "No global `fetch` — pass one in via { fetch } or upgrade to Node >= 18.",
      );
    }
    this.graph = new GraphNamespace(this);
    this.history = new HistoryNamespace(this);
  }

  /** Resolve the effective workspace id for a call. */
  resolveWorkspace(explicit?: string): string | undefined {
    return explicit ?? this.defaultWorkspaceId;
  }

  /** Low-level request helper. Exposed for advanced callers; prefer the
   *  typed methods on this class. */
  async request<T>(init: RequestInit): Promise<T> {
    const url = buildUrl(this.baseUrl, init.path, init.query);
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.apiKey}`,
      "User-Agent": USER_AGENT,
    };
    let body: string | undefined;
    if (init.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(init.body);
    }

    let lastErr: unknown;
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), this.timeoutMs);
      let resp: Response;
      try {
        resp = await this.fetchImpl(url, {
          method: init.method,
          headers,
          body,
          signal: controller.signal,
        });
      } catch (err) {
        clearTimeout(timer);
        lastErr = err;
        if (attempt >= this.maxRetries) {
          const msg = err instanceof Error ? err.message : String(err);
          throw new ServerError(`Network error: ${msg}`);
        }
        await sleep(backoffMs(attempt));
        continue;
      }
      clearTimeout(timer);
      if (RETRYABLE_STATUSES.has(resp.status) && attempt < this.maxRetries) {
        const ra = parseRetryAfter(resp.headers);
        await sleep(backoffMs(attempt, ra));
        continue;
      }
      if (!resp.ok) {
        await throwForStatus(resp);
      }
      return (await resp.json()) as T;
    }
    if (lastErr instanceof Error) throw new ServerError(`Retry budget exhausted: ${lastErr.message}`);
    throw new ServerError("Retry budget exhausted.");
  }

  // -------------------------------------------------------------------------
  // Top-level endpoints
  // -------------------------------------------------------------------------

  query(req: QueryRequest): Promise<QueryResponse> {
    const workspaceId = this.resolveWorkspace(req.workspaceId);
    return this.request<QueryResponse>({
      method: "POST",
      path: "/api/v1/ext/query",
      body: {
        workspace_id: workspaceId,
        query: req.query,
        session_id: req.sessionId,
        llm_model: req.llmModel,
        debug: req.debug ?? false,
      },
    });
  }
}

// ---------------------------------------------------------------------------
// Resource namespaces
// ---------------------------------------------------------------------------

class GraphNamespace {
  constructor(private readonly c: OpenGraphClient) {}

  stats(opts: { workspaceId?: string } = {}): Promise<GraphStats> {
    return this.c.request<GraphStats>({
      method: "GET",
      path: "/api/v1/ext/graph/stats",
      query: { workspace_id: this.c.resolveWorkspace(opts.workspaceId) ?? undefined },
    });
  }

  search(req: GraphSearchRequest): Promise<SearchResponse> {
    return this.c.request<SearchResponse>({
      method: "GET",
      path: "/api/v1/ext/graph/search",
      query: {
        q: req.q,
        workspace_id: this.c.resolveWorkspace(req.workspaceId) ?? undefined,
        top_k: req.topK ?? 10,
        node_type: req.nodeType,
      },
    });
  }

  node(nodeId: string, opts: { workspaceId?: string } = {}): Promise<NodeDetail> {
    return this.c.request<NodeDetail>({
      method: "GET",
      path: `/api/v1/ext/graph/node/${encodeURIComponent(nodeId)}`,
      query: { workspace_id: this.c.resolveWorkspace(opts.workspaceId) ?? undefined },
    });
  }

  traverse(req: TraverseRequestArgs): Promise<TraverseResponse> {
    return this.c.request<TraverseResponse>({
      method: "POST",
      path: "/api/v1/ext/graph/traverse",
      body: {
        workspace_id: this.c.resolveWorkspace(req.workspaceId),
        node_id: req.nodeId,
        max_depth: req.maxDepth ?? 3,
        edge_types: req.edgeTypes,
      },
    });
  }

  tree(opts: { workspaceId?: string } = {}): Promise<{ tree: unknown }> {
    return this.c.request<{ tree: unknown }>({
      method: "GET",
      path: "/api/v1/ext/graph/tree",
      query: { workspace_id: this.c.resolveWorkspace(opts.workspaceId) ?? undefined },
    });
  }

  tools(opts: {
    workspaceId?: string;
    category?: string;
    provider?: string;
    search?: string;
    limit?: number;
  } = {}): Promise<{ total: number; tools: Record<string, unknown>[] }> {
    return this.c.request({
      method: "GET",
      path: "/api/v1/ext/graph/tools",
      query: {
        workspace_id: this.c.resolveWorkspace(opts.workspaceId) ?? undefined,
        category: opts.category,
        provider: opts.provider,
        search: opts.search,
        limit: opts.limit ?? 50,
      },
    });
  }

  chapters(opts: { workspaceId?: string } = {}): Promise<{ chapters: Record<string, unknown>[] }> {
    return this.c.request({
      method: "GET",
      path: "/api/v1/ext/graph/chapters",
      query: { workspace_id: this.c.resolveWorkspace(opts.workspaceId) ?? undefined },
    });
  }
}

class HistoryNamespace {
  constructor(private readonly c: OpenGraphClient) {}

  async chats(opts: { workspaceId?: string; limit?: number } = {}): Promise<ChatSessionSummary[]> {
    const data = await this.c.request<{ sessions: ChatSessionSummary[] }>({
      method: "GET",
      path: "/api/v1/ext/history/chats",
      query: {
        workspace_id: this.c.resolveWorkspace(opts.workspaceId) ?? undefined,
        limit: opts.limit ?? 50,
      },
    });
    return data.sessions ?? [];
  }

  chat(sessionId: string, opts: { workspaceId?: string } = {}): Promise<ChatSessionDetail> {
    return this.c.request<ChatSessionDetail>({
      method: "GET",
      path: `/api/v1/ext/history/chats/${encodeURIComponent(sessionId)}`,
      query: { workspace_id: this.c.resolveWorkspace(opts.workspaceId) ?? undefined },
    });
  }

  async builds(opts: { workspaceId?: string; limit?: number } = {}): Promise<BuildJobSummary[]> {
    const data = await this.c.request<{ builds: BuildJobSummary[] }>({
      method: "GET",
      path: "/api/v1/ext/history/builds",
      query: {
        workspace_id: this.c.resolveWorkspace(opts.workspaceId) ?? undefined,
        limit: opts.limit ?? 50,
      },
    });
    return data.builds ?? [];
  }

  build(jobId: string): Promise<BuildJobSummary> {
    return this.c.request<BuildJobSummary>({
      method: "GET",
      path: `/api/v1/ext/history/builds/${encodeURIComponent(jobId)}`,
    });
  }
}
