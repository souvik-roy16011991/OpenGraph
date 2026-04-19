"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { Network } from "lucide-react";
import { Stepper } from "@/components/wizard/stepper";
import { CytoscapeView } from "@/components/graph/cytoscape-view";
import { NodeInspector } from "@/components/graph/node-inspector";
import { Legend } from "@/components/graph/legend";
import { FilterBar } from "@/components/graph/filter-bar";
import { api } from "@/lib/api";
import { useWizardStore } from "@/store/wizard-store";

export default function ExplorePage() {
  const vizQuery = useQuery({ queryKey: ["viz"], queryFn: () => api.visualization({ max_nodes: 2500 }) });
  const highlighted = useWizardStore((s) => s.highlightedNodeIds);
  const setHighlighted = useWizardStore((s) => s.setHighlightedNodeIds);
  const markCompleted = useWizardStore((s) => s.markCompleted);

  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [nodeTypes, setNodeTypes] = React.useState<Set<string>>(new Set());
  const [edgeTypes, setEdgeTypes] = React.useState<Set<string>>(new Set());

  React.useEffect(() => {
    if (vizQuery.data) markCompleted("explore", true);
  }, [vizQuery.data, markCompleted]);

  async function onSearch(q: string) {
    if (!q) {
      setHighlighted([]);
      return;
    }
    try {
      const res = await api.search(q, { top_k: 20 });
      setHighlighted(res.results.map((r) => String(r.node_id)));
    } catch {
      // ignore
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Network className="h-5 w-5" /> Graph explorer
          </h1>
          <p className="text-muted-foreground text-sm">
            {vizQuery.data
              ? `${vizQuery.data.nodes.length} nodes · ${vizQuery.data.edges.length} edges${vizQuery.data.truncated ? " (truncated)" : ""}`
              : "Loading graph…"}
          </p>
        </div>
        <Stepper current="explore" />
      </div>

      <div className="rounded-xl border bg-card overflow-hidden shadow-sm">
        <FilterBar
          nodeTypes={nodeTypes}
          onNodeTypesChange={setNodeTypes}
          edgeTypes={edgeTypes}
          onEdgeTypesChange={setEdgeTypes}
          onSearch={onSearch}
        />
        <div className="grid grid-cols-[1fr_340px] h-[640px]">
          <div className="relative">
            {vizQuery.isLoading && (
              <div className="absolute inset-0 flex items-center justify-center text-sm text-muted-foreground z-10">
                Loading graph…
              </div>
            )}
            {vizQuery.isError && (
              <div className="absolute inset-0 flex items-center justify-center text-sm text-destructive z-10">
                {(vizQuery.error as Error).message}
              </div>
            )}
            {vizQuery.data && (
              <CytoscapeView
                payload={vizQuery.data}
                onNodeClick={setSelectedId}
                highlightedIds={highlighted}
                nodeTypeFilter={nodeTypes}
                edgeTypeFilter={edgeTypes}
              />
            )}
            <Legend className="absolute right-3 bottom-3 z-10 max-w-[220px]" />
          </div>
          <div className="border-l bg-card/70">
            <NodeInspector nodeId={selectedId} onClose={() => setSelectedId(null)} onNavigate={setSelectedId} />
          </div>
        </div>
      </div>
    </div>
  );
}
