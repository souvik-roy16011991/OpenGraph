import { z } from "zod";

// ---------- Workspaces ----------
export interface WorkspaceSummary {
  id: string;
  name: string;
  description?: string | null;
  created_at: string;
  updated_at: string;
  file_counts: { knowledge: number; tool: number };
  last_build_at?: string | null;
  last_build_status?: string | null;
  stats?: { total_nodes?: number; total_edges?: number } | null;
}

export interface WorkspaceFileInfo {
  id: number;
  kb_source: "knowledge" | "tool";
  filename: string;
  size_bytes: number;
  chapters: number;
  title?: string | null;
  sha256: string;
  blob_url?: string | null;
  local_path?: string | null;
  active: boolean;
  created_at: string;
}

// ---------- Domain ----------
export const DomainPayloadSchema = z.object({
  domain_name: z.string().min(1, "Required"),
  domain_display_name: z.string().min(1, "Required"),
  organization_name: z.string().min(1, "Required"),
  knowledge_focus_examples: z.string().min(1, "Required"),
  tool_focus_examples: z.string().min(1, "Required"),
});
export type DomainPayload = z.infer<typeof DomainPayloadSchema>;

// ---------- Graph config ----------
export const EmbeddingsSchema = z.object({
  model: z.string(),
  dimensions: z.number().int().nullable().optional(),
  tfidf_fallback_dim: z.number().int(),
  similarity_threshold: z.number().min(0).max(1),
  max_related_edges_per_node: z.number().int().min(0).max(200),
  skip_related_to_types: z.array(z.string()),
  input_max_chars: z.number().int().min(16).max(8192),
  local_batch_size: z.number().int().min(1).max(1024),
  openrouter_batch_size: z.number().int().min(1).max(1024),
});
export const EdgeWeightsSchema = z.object({
  contains: z.number(),
  next_step: z.number(),
  integrates_with: z.number(),
  implements: z.number(),
  uses_tool: z.number(),
});
export const EdgesSchema = z.object({
  weights: EdgeWeightsSchema,
  min_tool_mention_length: z.number().int().min(1).max(64),
  enable_tool_name_aliases: z.boolean(),
});
export const CrossKbSchema = z.object({
  auto_threshold: z.number().min(0).max(1),
  embed_weight: z.number().min(0).max(1),
  cooccur_weight: z.number().min(0).max(1),
  max_links_per_chapter: z.number().int().min(0).max(50),
  chapter_embedding_max_chars: z.number().int().min(16).max(16384),
  min_tool_name_length: z.number().int().min(1).max(64),
  skip_chapter_headings: z.array(z.string()),
  llm_max_tokens: z.number().int().min(64).max(16384),
});
export const TraversalSchema = z.object({
  max_depth: z.number().int().min(1).max(20),
  max_nodes: z.number().int().min(1).max(2000),
  subtree_max_depth: z.number().int().min(1).max(10),
  full_tree_depth: z.number().int().min(1).max(10),
  top_k_entry_nodes: z.number().int().min(1).max(100),
});
export const SearchSchema = z.object({
  default_top_k: z.number().int().min(1).max(200),
  keyword_min_token_length: z.number().int().min(1).max(20),
  hybrid_semantic_weight: z.number().min(0).max(1),
  hybrid_keyword_weight: z.number().min(0).max(1),
});
export const ExtractionSchema = z.object({
  section_summary_max_chars: z.number().int().min(16).max(8192),
  max_paragraphs_per_section: z.number().int().min(1).max(200),
  toolnode_summary_max_chars: z.number().int().min(16).max(4096),
  processnode_summary_max_chars: z.number().int().min(16).max(4096),
  glossarynode_summary_max_chars: z.number().int().min(16).max(4096),
  tablenode_summary_max_headers: z.number().int().min(1).max(100),
  max_slug_length: z.number().int().min(4).max(200),
});
export const GraphConfigPayloadSchema = z.object({
  embeddings: EmbeddingsSchema,
  edges: EdgesSchema,
  cross_kb: CrossKbSchema,
  traversal: TraversalSchema,
  search: SearchSchema,
  extraction: ExtractionSchema,
});
export type GraphConfigPayload = z.infer<typeof GraphConfigPayloadSchema>;

export interface PutGraphConfigResponse {
  status: "applied" | "requires_rebuild";
  path: string;
  changed_sections: string[];
  requires_rebuild: boolean;
}

// ---------- Upload ----------
export interface UploadedFileInfo {
  id: number;
  kb_source: "knowledge" | "tool";
  filename: string;
  size_bytes: number;
  chapters: number;
  title?: string | null;
  sha256: string;
  local_path: string;
  blob_url?: string | null;
  blob_error?: string | null;
  duplicate: boolean;
}
export interface UploadResponse {
  workspace_id: string;
  knowledge: UploadedFileInfo[];
  tool: UploadedFileInfo[];
  warnings: string[];
}

// ---------- Build ----------
export interface StartBuildRequest {
  skip_embeddings: boolean;
  skip_llm_cross_links: boolean;
}
export interface BuildJobStatus {
  job_id: string;
  status: "queued" | "running" | "done" | "error";
  stage: number;
  stage_name: string;
  percent: number;
  started_at: number;
  finished_at: number | null;
  error: string | null;
  log_tail: string[];
  skip_embeddings: boolean;
  skip_llm_cross_links: boolean;
}

// ---------- Graph data ----------
export interface VizNode {
  id: string;
  type: string;
  kb_source: string;
  heading: string;
  summary: string;
  level: number;
  parent_id: string | null;
}
export interface VizEdge {
  source: string;
  target: string;
  edge_type: string;
  weight: number;
}
export interface GraphVizPayload {
  nodes: VizNode[];
  edges: VizEdge[];
  total_nodes: number;
  total_edges: number;
  truncated: boolean;
}

export interface GraphStats {
  total_nodes: number;
  total_edges: number;
  nodes_by_type: Record<string, number>;
  edges_by_type: Record<string, number>;
  backends?: { graph: string; vectors: string };
}

// ---------- History ----------
export interface BuildHistoryRow {
  job_id: string;
  status: "queued" | "running" | "done" | "error";
  stage: number;
  stage_name: string;
  percent: number;
  started_at: string | null;
  finished_at: string | null;
  duration_s: number | null;
  skip_embeddings: boolean;
  skip_llm_cross_links: boolean;
  error: string | null;
  stats: Record<string, unknown> | null;
  backends: { graph?: string; vectors?: string } | null;
  created_at: string;
}
export interface BuildHistoryDetail extends BuildHistoryRow {
  log_tail: string[];
  domain_snapshot: Record<string, unknown> | null;
  graph_snapshot: Record<string, unknown> | null;
}
export interface ChatHistoryRow {
  session_id: string;
  title: string | null;
  message_count: number;
  created_at: string;
  last_activity_at: string;
}
export interface ChatHistoryDetail {
  session_id: string;
  title: string | null;
  created_at: string;
  last_activity_at: string;
  messages: Array<{
    id: number;
    role: "user" | "assistant";
    query: string | null;
    response: Record<string, unknown> | null;
    intent: string | null;
    kb_focus: string | null;
    tools_referenced: unknown[] | null;
    knowledge_concepts: unknown[] | null;
    traversal_path: string[] | null;
    follow_up_suggestions: string[] | null;
    error: string | null;
    duration_ms: number;
    created_at: string;
  }>;
}
export interface ConfigHistoryRow {
  id: number;
  kind: "domain" | "graph";
  changed_sections: string[] | null;
  requires_rebuild: boolean | null;
  yaml_preview: string;
  created_at: string;
}
export interface UploadHistoryRow {
  id: number;
  kb_source: "knowledge" | "tool";
  filename: string;
  size_bytes: number;
  chapters: number;
  title: string | null;
  sha256: string;
  blob_url: string | null;
  blob_error: string | null;
  created_at: string;
}

export interface NodeDetail {
  node: Record<string, unknown> & {
    node_id: string;
    node_type: string;
    heading: string;
    content_summary?: string;
    raw_text?: string;
    kb_source?: string;
  };
  edges: Array<{
    target?: string;
    source?: string;
    edge_type: string;
    weight: number;
    [k: string]: unknown;
  }>;
  children: string[];
  parent: string | null;
}

export interface SearchResponse {
  query: string;
  results: Array<Record<string, unknown> & { node_id: string; heading: string; search_score: number }>;
}

// ---------- Templates ----------
export interface KBTemplate {
  slug: string;
  name: string;
  description: string;
  category: string;
  icon: string;
  domain: {
    domain_name: string;
    domain_display_name: string;
    organization_name: string;
    knowledge_focus_examples: string;
    tool_focus_examples: string;
  };
}
export interface InstantiateTemplateResponse {
  id: string;
  name: string;
  description: string | null;
  template_slug: string;
}

// ---------- LLM selection ----------
export interface LLMModel {
  id: string;
  name: string;
  description?: string | null;
  context_length?: number | null;
  pricing?: {
    prompt?: string | number | null;
    completion?: string | number | null;
  };
}
export interface LLMModelsResponse {
  default: string;
  models: LLMModel[];
  allowlist_active: boolean;
}
export interface WorkspaceLLMResponse {
  workspace_id: string;
  llm_model: string | null;
  effective_model: string;
}

// ---------- Agent query ----------
export interface QueryRequest {
  query: string;
  stream?: boolean;
  session_id?: string;
  llm_model?: string | null;
}
export interface QueryResponse {
  query: string;
  intent: string;
  kb_focus: string;
  extracted_topics: string[];
  response: string;
  steps: Array<Record<string, unknown>>;
  tools_referenced: Array<Record<string, unknown>>;
  knowledge_concepts: Array<Record<string, unknown>>;
  follow_up_suggestions: string[];
  traversal_path: string[];
  session_id?: string | null;
  duration_ms?: number | null;
  error?: string | null;
  llm_model?: string | null;
}
