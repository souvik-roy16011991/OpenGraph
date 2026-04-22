"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, XCircle, Loader2, Clock, ChevronRight } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatDuration, formatNumber } from "@/lib/utils";
import { api } from "@/lib/api";
import { parseBuildStats, type BuildHistoryRow } from "@/lib/schema";
import { BuildDetailDrawer } from "./build-detail-drawer";

function StatusIcon({ status }: { status: BuildHistoryRow["status"] }) {
  if (status === "done") return <CheckCircle2 className="h-4 w-4 text-emerald-500" />;
  if (status === "error") return <XCircle className="h-4 w-4 text-destructive" />;
  if (status === "running" || status === "queued") return <Loader2 className="h-4 w-4 animate-spin text-sky-500" />;
  return <Clock className="h-4 w-4" />;
}

/** Tiny inline metrics strip. Shown on every row; hides bytes-like fields
 *  on narrow viewports to avoid overflow (see plan §frontend row layout). */
function MetricsStrip({ row }: { row: BuildHistoryRow }) {
  const s = parseBuildStats(row.stats);
  const nodes = s.total_nodes;
  const edges = s.total_edges;
  const tokens = s.usage?.llm_total_tokens;
  const vectors = s.usage?.embedding_vectors ?? s.usage?.embedding_vectors;
  const duration = row.duration_s;

  // If we have no metrics at all (in-flight or pre-feature build), render nothing.
  if (
    tokens === undefined &&
    nodes === undefined &&
    edges === undefined &&
    vectors === undefined &&
    duration === null
  ) {
    return null;
  }

  const items: Array<{ label: string; value: string; hideOnNarrow?: boolean }> = [];
  if (duration !== null && duration !== undefined) {
    items.push({ label: "time", value: formatDuration(duration) });
  }
  if (tokens !== undefined) {
    items.push({ label: "tokens", value: formatNumber(tokens) });
  }
  if (nodes !== undefined || edges !== undefined) {
    items.push({
      label: "graph",
      value: `${formatNumber(nodes ?? 0)}n / ${formatNumber(edges ?? 0)}e`,
      hideOnNarrow: true,
    });
  }
  if (vectors !== undefined && vectors > 0) {
    items.push({ label: "vectors", value: formatNumber(vectors), hideOnNarrow: true });
  }

  return (
    <div className="mt-1 flex items-center gap-3 flex-wrap text-xs text-muted-foreground">
      {items.map((it, idx) => (
        <span
          key={it.label}
          className={`inline-flex items-center gap-1 tabular-nums ${
            it.hideOnNarrow ? "hidden sm:inline-flex" : ""
          }`}
        >
          {idx > 0 && <span className="text-muted-foreground/40">·</span>}
          <span className="uppercase tracking-wide text-[10px] text-muted-foreground/70">
            {it.label}
          </span>
          <span className="font-mono">{it.value}</span>
        </span>
      ))}
    </div>
  );
}

export function BuildsTable() {
  const q = useQuery({ queryKey: ["history", "builds"], queryFn: () => api.historyBuilds(100), refetchInterval: 5000 });
  const [openJobId, setOpenJobId] = useState<string | null>(null);

  if (q.isLoading) return <p className="text-sm text-muted-foreground">Loading build history…</p>;
  if (q.isError) return <p className="text-sm text-destructive">{(q.error as Error).message}</p>;
  const rows = q.data?.builds ?? [];
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No builds recorded yet.</p>;

  return (
    <>
      <div className="grid gap-2">
        {rows.map((r) => (
          <Card
            key={r.job_id}
            className="px-4 py-3 cursor-pointer transition-colors hover:bg-accent/40"
            onClick={() => setOpenJobId(r.job_id)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setOpenJobId(r.job_id);
              }
            }}
          >
            <div className="flex items-start gap-4">
              <StatusIcon status={r.status} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-mono text-xs">{r.job_id}</span>
                  <Badge variant={r.status === "done" ? "success" : r.status === "error" ? "destructive" : "secondary"} className="text-[10px]">
                    {r.status}
                  </Badge>
                  {r.backends?.graph && <Badge variant="outline" className="text-[10px] font-mono">graph: {r.backends.graph}</Badge>}
                  {r.backends?.vectors && <Badge variant="outline" className="text-[10px] font-mono">vec: {r.backends.vectors}</Badge>}
                  {r.skip_embeddings && <Badge variant="outline" className="text-[10px]">no-embed</Badge>}
                </div>
                <MetricsStrip row={r} />
                {r.error && <p className="text-xs text-destructive mt-1 truncate">{r.error}</p>}
              </div>
              <div className="flex items-start gap-2">
                <div className="text-right text-[10px] text-muted-foreground font-mono tabular-nums whitespace-nowrap">
                  <div>{new Date(r.created_at).toLocaleString()}</div>
                </div>
                <ChevronRight className="h-4 w-4 text-muted-foreground/50 mt-[1px]" aria-hidden />
              </div>
            </div>
          </Card>
        ))}
      </div>
      <BuildDetailDrawer
        jobId={openJobId}
        open={openJobId !== null}
        onOpenChange={(next) => {
          if (!next) setOpenJobId(null);
        }}
      />
    </>
  );
}
