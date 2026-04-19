"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, XCircle, Loader2, Clock } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatDuration } from "@/lib/utils";
import { api } from "@/lib/api";
import type { BuildHistoryRow } from "@/lib/schema";

function StatusIcon({ status }: { status: BuildHistoryRow["status"] }) {
  if (status === "done") return <CheckCircle2 className="h-4 w-4 text-emerald-500" />;
  if (status === "error") return <XCircle className="h-4 w-4 text-destructive" />;
  if (status === "running" || status === "queued") return <Loader2 className="h-4 w-4 animate-spin text-sky-500" />;
  return <Clock className="h-4 w-4" />;
}

export function BuildsTable() {
  const q = useQuery({ queryKey: ["history", "builds"], queryFn: () => api.historyBuilds(100), refetchInterval: 5000 });
  if (q.isLoading) return <p className="text-sm text-muted-foreground">Loading build history…</p>;
  if (q.isError) return <p className="text-sm text-destructive">{(q.error as Error).message}</p>;
  const rows = q.data?.builds ?? [];
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No builds recorded yet.</p>;

  return (
    <div className="grid gap-2">
      {rows.map((r) => (
        <Card key={r.job_id} className="px-4 py-3">
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
              {r.stats && (
                <p className="text-xs text-muted-foreground mt-1">
                  {String((r.stats as { total_nodes?: number }).total_nodes ?? "?")} nodes ·{" "}
                  {String((r.stats as { total_edges?: number }).total_edges ?? "?")} edges
                </p>
              )}
              {r.error && <p className="text-xs text-destructive mt-1 truncate">{r.error}</p>}
            </div>
            <div className="text-right text-[10px] text-muted-foreground font-mono tabular-nums whitespace-nowrap">
              <div>{new Date(r.created_at).toLocaleString()}</div>
              {r.duration_s !== null && <div>{formatDuration(r.duration_s)}</div>}
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}
