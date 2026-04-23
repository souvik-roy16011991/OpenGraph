"use client";

import { useQuery } from "@tanstack/react-query";
import { FileText, ExternalLink } from "lucide-react";

import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { formatBytes } from "@/lib/utils";
import { api } from "@/lib/api";

export function UploadsTable() {
  const q = useQuery({ queryKey: ["history", "uploads"], queryFn: () => api.historyUploads(undefined, 100), refetchInterval: 10000 });
  if (q.isLoading) {
    return (
      <div className="grid gap-2">
        {[0, 1, 2, 3].map((i) => (
          <Card key={i} className="px-4 py-3">
            <div className="flex items-start gap-3">
              <Skeleton className="h-4 w-4 mt-0.5" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-2/5" />
                <Skeleton className="h-3 w-1/3" />
              </div>
              <Skeleton className="h-3 w-24" />
            </div>
          </Card>
        ))}
      </div>
    );
  }
  if (q.isError) return <p className="text-sm text-destructive">{(q.error as Error).message}</p>;
  const rows = q.data?.uploads ?? [];
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No uploads recorded yet.</p>;

  return (
    <div className="grid gap-2">
      {rows.map((r) => (
        <Card key={r.id} className="px-4 py-3">
          <div className="flex items-start gap-3">
            <FileText className="h-4 w-4 text-muted-foreground mt-0.5 shrink-0" />

            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge variant="secondary" className="text-[10px]">{r.kb_source}</Badge>
                <span className="text-sm font-medium truncate">{r.filename}</span>
              </div>
              <div className="flex items-center gap-3 mt-1 text-[11px] text-muted-foreground">
                <span>{formatBytes(r.size_bytes)}</span>
                <span>{r.chapters} chapters</span>
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
