"use client";

import { useQuery } from "@tanstack/react-query";
import { FileJson, ExternalLink } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { formatBytes } from "@/lib/utils";
import { api } from "@/lib/api";

export function UploadsTable() {
  const q = useQuery({ queryKey: ["history", "uploads"], queryFn: () => api.historyUploads(undefined, 100), refetchInterval: 10000 });
  if (q.isLoading) return <p className="text-sm text-muted-foreground">Loading upload history…</p>;
  if (q.isError) return <p className="text-sm text-destructive">{(q.error as Error).message}</p>;
  const rows = q.data?.uploads ?? [];
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No uploads recorded yet.</p>;

  return (
    <div className="grid gap-2">
      {rows.map((r) => (
        <Card key={r.id} className="px-4 py-3">
          <div className="flex items-start gap-3">
            <FileJson className="h-4 w-4 text-muted-foreground mt-0.5 shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant="secondary" className="text-[10px]">{r.kb_source}</Badge>
                <span className="text-sm font-medium truncate">{r.filename}</span>
              </div>
              <div className="flex items-center gap-3 mt-1 text-[11px] text-muted-foreground">
                <span>{formatBytes(r.size_bytes)}</span>
                <span>{r.chapters} chapters</span>
                <span className="font-mono">sha256: {r.sha256.slice(0, 12)}…</span>
                {r.blob_url && (
                  <a href={r.blob_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 hover:text-foreground">
                    blob <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </div>
              {r.blob_error && <p className="text-xs text-destructive mt-1 truncate">blob error: {r.blob_error}</p>}
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
