/**
 * Public type definitions for the OpenGraph developer API.
 *
 * Matches src/api/ext_models.py one-for-one. Hand-written for better DX
 * than codegen would produce (no `components["schemas"]["…"]` noise), but
 * the field set mirrors the wire format exactly so the shapes round-trip
 * with the Python SDK.
 *
 * Unknown fields on the wire are preserved via `readonly [key: string]:
 * unknown` so adding a new v1 response field never breaks older SDK
 * versions.
 */

export type KBFocus = "knowledge" | "tool" | "both";

export interface ChatUsage {
  llm_prompt_tokens: number;
  llm_completion_tokens: number;
  llm_total_tokens: number;
  llm_calls: number;
  model?: string | null;
}

export interface QueryResponse {
  session_id: string;
  response: string;
  intent: string;
  kb_focus: KBFocus;
  extracted_topics: string[];
  follow_up_suggestions: string[];
  llm_model?: string | null;
  usage?: ChatUsage | null;
  duration_ms: number;
  history_persisted: boolean;

  /** Present only when `debug: true` was passed. Shape is not stable. */
  steps?: Record<string, unknown>[] | null;
  traversal_path?: string[] | null;
  knowledge_concepts?: Record<string, unknown>[] | null;
  tools_referenced?: Record<string, unknown>[] | null;
}

export interface GraphStats {
  total_nodes: number;
  total_edges: number;
  nodes_by_type: Record<string, number>;
  edges_by_type: Record<string, number>;
  backends: Record<string, unknown>;
}

export interface SearchResult {
  node_id: string;
  node_type: string;
  heading: string;
  kb_source: string;
  score: number;
}

export interface SearchResponse {
  query: string;
  results: SearchResult[];
}

export interface NodeDetail {
  node: Record<string, unknown>;
  edges: Record<string, unknown>[];
  children: Record<string, unknown>[];
  parent?: string | null;
}

export interface TraverseResponse {
  root_node_id: string;
  traversal_path: string[];
  nodes: Record<string, unknown>[];
  edge_count: number;
}

export interface ChatSessionSummary {
  session_id: string;
  title?: string | null;
  message_count: number;
  created_at: string;
  last_activity_at: string;
}

export interface ChatMessage {
  id: number;
  role: "user" | "assistant";
  query?: string | null;
  response?: Record<string, unknown> | null;
  intent?: string | null;
  kb_focus?: string | null;
  created_at: string;
  duration_ms: number;
}

export interface ChatSessionDetail {
  session_id: string;
  workspace_id: string;
  title?: string | null;
  messages: ChatMessage[];
}

export interface BuildJobSummary {
  job_id: string;
  workspace_id: string;
  status: "queued" | "running" | "done" | "error" | string;
  stage: number;
  stage_name: string;
  percent: number;
  created_at: string;
  finished_at?: string | null;
}

// ---------------------------------------------------------------------------
// Request types
// ---------------------------------------------------------------------------

export interface QueryRequest {
  /** Target workspace UUID. Optional if the client was constructed with a default. */
  workspaceId?: string;
  /** Natural-language question. */
  query: string;
  /** Continue an existing chat session; omit for a new one. */
  sessionId?: string;
  /** Override the LLM model for this single call. */
  llmModel?: string;
  /** When true, include internal reasoning steps (unstable shape). */
  debug?: boolean;
}

export interface GraphSearchRequest {
  q: string;
  workspaceId?: string;
  topK?: number;
  nodeType?: string;
}

export interface TraverseRequestArgs {
  nodeId: string;
  workspaceId?: string;
  maxDepth?: number;
  edgeTypes?: string[];
}
