"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowRight, Sliders, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import { ModelSelect } from "@/components/ui/model-select";
import { WizardPage } from "@/components/wizard/wizard-page";
import { api } from "@/lib/api";
import type { GraphConfigPayload } from "@/lib/schema";
import { useRequireWorkspace } from "@/hooks/use-require-workspace";
import { useWizardStore } from "@/store/wizard-store";
import { FormField, NumberSlider, ChipsEditor, Toggle } from "@/components/forms/graph-config-form";

const NODE_TYPE_OPTIONS = ["domain", "chapter", "section", "table", "tool", "process", "glossary", "concept"];

export default function GraphConfigPage() {
  const router = useRouter();
  const activeWs = useRequireWorkspace();
  const markCompleted = useWizardStore((s) => s.markCompleted);

  const query = useQuery({
    queryKey: ["graph-config", activeWs],
    queryFn: api.getGraphCfg,
    enabled: Boolean(activeWs),
  });
  const embedModelsQuery = useQuery({
    queryKey: ["embedding-models"],
    queryFn: () => api.listEmbeddingModels(),
    staleTime: 5 * 60 * 1000,
  });
  const [cfg, setCfg] = React.useState<GraphConfigPayload | null>(null);

  React.useEffect(() => {
    if (query.data && !cfg) setCfg(query.data);
  }, [query.data, cfg]);

  const mutation = useMutation({
    mutationFn: (p: GraphConfigPayload) => api.putGraphCfg(p),
    onSuccess: (r) => {
      toast.success(
        r.requires_rebuild ? "Saved — rebuild required" : "Saved — took effect immediately",
        { description: r.changed_sections.length ? `Changed: ${r.changed_sections.join(", ")}` : undefined }
      );
      markCompleted("graph-config", true);
      setTimeout(() => router.push("/build"), 400);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  if (!cfg || query.isLoading) {
    return <div className="text-sm text-muted-foreground p-8">Loading configuration…</div>;
  }
  if (query.isError) return <div className="text-sm text-destructive p-8">Failed to load: {(query.error as Error).message}</div>;

  function patch<K extends keyof GraphConfigPayload>(section: K, update: Partial<GraphConfigPayload[K]>) {
    setCfg((prev) => (prev ? { ...prev, [section]: { ...prev[section], ...update } } : prev));
  }

  const e = cfg.embeddings;
  const ed = cfg.edges;
  const w = ed.weights;
  const x = cfg.cross_kb;
  const t = cfg.traversal;
  const s = cfg.search;
  const ex = cfg.extraction;

  return (
    <WizardPage
      icon={Sliders}
      title="Graph configuration"
      description={
        <>
          Tune how the graph is built. Changes in Embeddings / Edges /
          Cross-KB / Extraction require a rebuild. Traversal &amp; Search
          apply live.
        </>
      }
      // Tabs + number sliders + chip editors read better in a wider column.
      maxWidthClass="max-w-5xl"
    >
      <Tabs defaultValue="embeddings" className="w-full">
        <TabsList className="flex flex-wrap h-auto">
          <TabsTrigger value="embeddings">Embeddings</TabsTrigger>
          <TabsTrigger value="edges">Edges</TabsTrigger>
          <TabsTrigger value="cross_kb">Cross-KB</TabsTrigger>
          <TabsTrigger value="traversal">Traversal <Badge variant="success" className="ml-2 text-[9px]">live</Badge></TabsTrigger>
          <TabsTrigger value="search">Search <Badge variant="success" className="ml-2 text-[9px]">live</Badge></TabsTrigger>
          <TabsTrigger value="extraction">Extraction</TabsTrigger>
        </TabsList>

        <TabsContent value="embeddings">
          <Card>
            <CardHeader>
              <CardTitle>Embeddings & semantic edges</CardTitle>
              <CardDescription>Controls density of RELATED_TO edges and embedding quality.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-5 md:grid-cols-2">
              <FormField label="Model" hint="Pick an OpenRouter embedding model. Plain sentence-transformers names still work via env EMBEDDING_MODEL.">
                <ModelSelect
                  models={embedModelsQuery.data?.models ?? (e.model ? [{ id: e.model, name: e.model }] : [])}
                  value={e.model}
                  onChange={(id) => patch("embeddings", { model: id })}
                  defaultModel={embedModelsQuery.data?.default}
                  loading={embedModelsQuery.isLoading}
                />
              </FormField>
              <FormField label="Dimensions (Matryoshka)" hint="Override embedding size. Leave blank for model-native.">
                <Input
                  type="number"
                  value={e.dimensions ?? ""}
                  placeholder="(native)"
                  onChange={(ev) => {
                    const v = ev.target.value;
                    patch("embeddings", { dimensions: v === "" ? null : Number(v) });
                  }}
                />
              </FormField>
              <FormField label="Similarity threshold" hint="Min cosine sim for RELATED_TO. Higher = sparser graph.">
                <NumberSlider value={e.similarity_threshold} min={0} max={1} step={0.01} onChange={(v) => patch("embeddings", { similarity_threshold: v })} />
              </FormField>
              <FormField label="Max RELATED_TO edges per node" hint="Fan-out cap. Prevents hub explosion.">
                <NumberSlider value={e.max_related_edges_per_node} min={0} max={50} onChange={(v) => patch("embeddings", { max_related_edges_per_node: v })} />
              </FormField>
              <FormField label="Input max chars" hint="Heading+summary truncation before embedding.">
                <NumberSlider value={e.input_max_chars} min={64} max={4096} step={32} onChange={(v) => patch("embeddings", { input_max_chars: v })} />
              </FormField>
              <FormField label="Skip RELATED_TO for types" hint="Node types that never get semantic edges." className="md:col-span-2">
                <ChipsEditor values={e.skip_related_to_types} onChange={(v) => patch("embeddings", { skip_related_to_types: v })} options={NODE_TYPE_OPTIONS} placeholder="Add type…" />
              </FormField>
              <FormField label="Local batch size" hint="sentence-transformers encode batch.">
                <NumberSlider value={e.local_batch_size} min={1} max={512} onChange={(v) => patch("embeddings", { local_batch_size: v })} />
              </FormField>
              <FormField label="OpenRouter batch size" hint="Remote API batch.">
                <NumberSlider value={e.openrouter_batch_size} min={1} max={128} onChange={(v) => patch("embeddings", { openrouter_batch_size: v })} />
              </FormField>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="edges">
          <Card>
            <CardHeader>
              <CardTitle>Edge weights & matching</CardTitle>
              <CardDescription>How strong each edge type is, and tool-mention detection.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-5 md:grid-cols-2">
              <FormField label="CONTAINS weight" hint="Parent → child hierarchy.">
                <NumberSlider value={w.contains} min={0} max={2} step={0.05} onChange={(v) => patch("edges", { weights: { ...w, contains: v } })} />
              </FormField>
              <FormField label="NEXT_STEP weight" hint="Sequential process steps.">
                <NumberSlider value={w.next_step} min={0} max={2} step={0.05} onChange={(v) => patch("edges", { weights: { ...w, next_step: v } })} />
              </FormField>
              <FormField label="INTEGRATES_WITH weight" hint="Tool → tool (from Connected Systems).">
                <NumberSlider value={w.integrates_with} min={0} max={2} step={0.05} onChange={(v) => patch("edges", { weights: { ...w, integrates_with: v } })} />
              </FormField>
              <FormField label="IMPLEMENTS weight" hint="Tool chapter → knowledge chapter (cross-KB).">
                <NumberSlider value={w.implements} min={0} max={2} step={0.05} onChange={(v) => patch("edges", { weights: { ...w, implements: v } })} />
              </FormField>
              <FormField label="USES_TOOL weight" hint="Knowledge section → tool (text mention).">
                <NumberSlider value={w.uses_tool} min={0} max={2} step={0.05} onChange={(v) => patch("edges", { weights: { ...w, uses_tool: v } })} />
              </FormField>
              <FormField label="Min tool mention length" hint="Lower = more USES_TOOL edges but more false positives.">
                <NumberSlider value={ed.min_tool_mention_length} min={1} max={20} onChange={(v) => patch("edges", { min_tool_mention_length: v })} />
              </FormField>
              <FormField label="Tool name aliases" hint="For 'Experian Bureau', also match 'Experian' in prose.">
                <Toggle value={ed.enable_tool_name_aliases} onChange={(v) => patch("edges", { enable_tool_name_aliases: v })} />
              </FormField>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="cross_kb">
          <Card>
            <CardHeader>
              <CardTitle>Cross-KB IMPLEMENTS links</CardTitle>
              <CardDescription>Balance precision vs recall when linking tool chapters to knowledge chapters.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-5 md:grid-cols-2">
              <FormField label="Auto threshold" hint="Min combined score to accept a link.">
                <NumberSlider value={x.auto_threshold} min={0} max={1} step={0.01} onChange={(v) => patch("cross_kb", { auto_threshold: v })} />
              </FormField>
              <FormField label="Max links per chapter" hint="Top-N scoring only.">
                <NumberSlider value={x.max_links_per_chapter} min={0} max={20} onChange={(v) => patch("cross_kb", { max_links_per_chapter: v })} />
              </FormField>
              <FormField label="Embed weight" hint="Cosine similarity of chapter embeddings.">
                <NumberSlider value={x.embed_weight} min={0} max={1} step={0.05} onChange={(v) => patch("cross_kb", { embed_weight: v })} />
              </FormField>
              <FormField label="Co-occurrence weight" hint="% of tool names in tool chapter found in knowledge chapter text.">
                <NumberSlider value={x.cooccur_weight} min={0} max={1} step={0.05} onChange={(v) => patch("cross_kb", { cooccur_weight: v })} />
              </FormField>
              <FormField label="Chapter embedding max chars" hint="Raw text truncation for chapter embedding.">
                <NumberSlider value={x.chapter_embedding_max_chars} min={100} max={8000} step={50} onChange={(v) => patch("cross_kb", { chapter_embedding_max_chars: v })} />
              </FormField>
              <FormField label="Min tool name length" hint="For the co-occurrence signal; filters tiny noise names.">
                <NumberSlider value={x.min_tool_name_length} min={1} max={20} onChange={(v) => patch("cross_kb", { min_tool_name_length: v })} />
              </FormField>
              <FormField label="Skip chapter headings" hint="Chapters excluded from cross-KB mapping." className="md:col-span-2">
                <ChipsEditor values={x.skip_chapter_headings} onChange={(v) => patch("cross_kb", { skip_chapter_headings: v })} placeholder="Add heading…" />
              </FormField>
              <FormField label="LLM max tokens" hint="Budget for cross-link refinement call.">
                <NumberSlider value={x.llm_max_tokens} min={128} max={8192} step={64} onChange={(v) => patch("cross_kb", { llm_max_tokens: v })} />
              </FormField>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="traversal">
          <Card>
            <CardHeader>
              <CardTitle>Traversal defaults</CardTitle>
              <CardDescription>How far traversals reach at query time. Live — no rebuild needed.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-5 md:grid-cols-2">
              <FormField label="Max BFS depth" hint="Upper bound for bfs_traverse().">
                <NumberSlider value={t.max_depth} min={1} max={15} onChange={(v) => patch("traversal", { max_depth: v })} />
              </FormField>
              <FormField label="Max nodes per traversal" hint="Result-set cap.">
                <NumberSlider value={t.max_nodes} min={5} max={500} onChange={(v) => patch("traversal", { max_nodes: v })} />
              </FormField>
              <FormField label="Subtree max depth" hint="get_subtree() depth for UI/API export.">
                <NumberSlider value={t.subtree_max_depth} min={1} max={8} onChange={(v) => patch("traversal", { subtree_max_depth: v })} />
              </FormField>
              <FormField label="Full tree depth" hint="full_tree() depth for top-level overview.">
                <NumberSlider value={t.full_tree_depth} min={1} max={6} onChange={(v) => patch("traversal", { full_tree_depth: v })} />
              </FormField>
              <FormField label="Top-K entry nodes" hint="Candidate entry points retrieved per query.">
                <NumberSlider value={t.top_k_entry_nodes} min={1} max={30} onChange={(v) => patch("traversal", { top_k_entry_nodes: v })} />
              </FormField>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="search">
          <Card>
            <CardHeader>
              <CardTitle>Search defaults</CardTitle>
              <CardDescription>Hybrid search knobs used by the agent's entry-node retrieval.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-5 md:grid-cols-2">
              <FormField label="Default top_k" hint="For keyword_search and semantic_search when callers don't specify.">
                <NumberSlider value={s.default_top_k} min={1} max={50} onChange={(v) => patch("search", { default_top_k: v })} />
              </FormField>
              <FormField label="Keyword min token length" hint="Filters short/common words in keyword search.">
                <NumberSlider value={s.keyword_min_token_length} min={1} max={10} onChange={(v) => patch("search", { keyword_min_token_length: v })} />
              </FormField>
              <FormField label="Hybrid semantic weight" hint="Weight on vector similarity score.">
                <NumberSlider value={s.hybrid_semantic_weight} min={0} max={1} step={0.05} onChange={(v) => patch("search", { hybrid_semantic_weight: v })} />
              </FormField>
              <FormField label="Hybrid keyword weight" hint="Weight on token-overlap score.">
                <NumberSlider value={s.hybrid_keyword_weight} min={0} max={1} step={0.05} onChange={(v) => patch("search", { hybrid_keyword_weight: v })} />
              </FormField>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="extraction">
          <Card>
            <CardHeader>
              <CardTitle>Node extraction truncation</CardTitle>
              <CardDescription>How much content to retain per node. Affects storage size and retrieval context.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-5 md:grid-cols-2">
              <FormField label="Section summary max chars" hint="SectionNode.content_summary truncation.">
                <NumberSlider value={ex.section_summary_max_chars} min={100} max={4000} step={50} onChange={(v) => patch("extraction", { section_summary_max_chars: v })} />
              </FormField>
              <FormField label="Max paragraphs per section" hint="Kept in SectionNode; rest still in raw_text.">
                <NumberSlider value={ex.max_paragraphs_per_section} min={1} max={100} onChange={(v) => patch("extraction", { max_paragraphs_per_section: v })} />
              </FormField>
              <FormField label="ToolNode summary max chars" hint="Trims tool purpose text.">
                <NumberSlider value={ex.toolnode_summary_max_chars} min={50} max={2000} step={25} onChange={(v) => patch("extraction", { toolnode_summary_max_chars: v })} />
              </FormField>
              <FormField label="ProcessNode summary max chars" hint="Trims step description.">
                <NumberSlider value={ex.processnode_summary_max_chars} min={50} max={2000} step={25} onChange={(v) => patch("extraction", { processnode_summary_max_chars: v })} />
              </FormField>
              <FormField label="GlossaryNode summary max chars" hint="Trims definitions.">
                <NumberSlider value={ex.glossarynode_summary_max_chars} min={50} max={2000} step={25} onChange={(v) => patch("extraction", { glossarynode_summary_max_chars: v })} />
              </FormField>
              <FormField label="TableNode summary max headers" hint="Headers shown in table preview.">
                <NumberSlider value={ex.tablenode_summary_max_headers} min={1} max={30} onChange={(v) => patch("extraction", { tablenode_summary_max_headers: v })} />
              </FormField>
              <FormField label="Max slug length" hint="Max length of id slugs. Changing invalidates existing IDs!">
                <NumberSlider value={ex.max_slug_length} min={4} max={100} onChange={(v) => patch("extraction", { max_slug_length: v })} />
              </FormField>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <AlertTriangle className="h-3 w-3 text-amber-500" />
          Saving to <code className="font-mono">kb-config/graph.yaml</code> and clearing caches.
        </div>
        <Button size="lg" onClick={() => mutation.mutate(cfg)} disabled={mutation.isPending}>
          {mutation.isPending ? "Saving…" : "Save & continue"} <ArrowRight className="h-4 w-4" />
        </Button>
      </div>
    </WizardPage>
  );
}
