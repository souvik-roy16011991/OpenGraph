"use client";

import { useQuery } from "@tanstack/react-query";
import { FileCog } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

export function ConfigsTable() {
  const q = useQuery({ queryKey: ["history", "configs"], queryFn: () => api.historyConfigs(undefined, 100), refetchInterval: 10000 });
  if (q.isLoading) return <p className="text-sm text-muted-foreground">Loading config history…</p>;
  if (q.isError) return <p className="text-sm text-destructive">{(q.error as Error).message}</p>;
  const rows = q.data?.configs ?? [];
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No config edits recorded yet.</p>;

  return (
    <div className="grid gap-2">
      {rows.map((r) => (
        <Card key={r.id} className="px-4 py-3">
          <div className="flex items-start gap-3">
            <FileCog className="h-4 w-4 text-muted-foreground mt-0.5 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant="secondary" className="text-[10px]">{r.kind}</Badge>
                {r.requires_rebuild && <Badge variant="warning" className="text-[10px]">rebuild required</Badge>}
                {(r.changed_sections || []).map((s) => (
                  <Badge key={s} variant="outline" className="text-[10px] font-mono">{s}</Badge>
                ))}
              </div>
              {r.yaml_preview && (
                <pre className="mt-2 p-2 rounded bg-muted/40 text-[10px] font-mono leading-snug whitespace-pre-wrap line-clamp-4">
{r.yaml_preview}
                </pre>
              )}
            </div>
            <div className="text-right text-[10px] text-muted-foreground font-mono tabular-nums whitespace-nowrap">
              {new Date(r.created_at).toLocaleString()}
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}
