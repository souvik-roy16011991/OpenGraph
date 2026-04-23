"use client";

import { useQuery } from "@tanstack/react-query";
import { MessageSquare } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import { formatNumber } from "@/lib/utils";

export function ChatsTable() {
  const q = useQuery({ queryKey: ["history", "chats"], queryFn: () => api.historyChats(100), refetchInterval: 5000 });
  if (q.isLoading) {
    return (
      <div className="grid gap-2">
        {[0, 1, 2, 3, 4].map((i) => (
          <Card key={i} className="px-4 py-3">
            <div className="flex items-start gap-3">
              <Skeleton className="h-4 w-4 rounded mt-0.5" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-2/5" />
                <Skeleton className="h-3 w-1/2" />
              </div>
              <Skeleton className="h-3 w-24" />
            </div>
          </Card>
        ))}
      </div>
    );
  }
  if (q.isError) return <p className="text-sm text-destructive">{(q.error as Error).message}</p>;
  const rows = q.data?.chats ?? [];
  if (rows.length === 0) return <p className="text-sm text-muted-foreground">No chats recorded yet.</p>;

  return (
    <div className="grid gap-2">
      {rows.map((r) => (
        <Card key={r.session_id} className="px-4 py-3">
          <div className="flex items-start gap-3">
            <MessageSquare className="h-4 w-4 text-muted-foreground mt-0.5 shrink-0" />
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium truncate">{r.title || "(untitled session)"}</p>
              <div className="flex items-center gap-3 mt-1 flex-wrap text-xs text-muted-foreground">
                <Badge variant="outline" className="text-[10px] font-mono">{r.session_id.slice(0, 8)}…</Badge>
                <span className="tabular-nums">{r.message_count} messages</span>
                {r.usage && r.usage.llm_total_tokens > 0 && (
                  <>
                    <span className="text-muted-foreground/40">·</span>
                    <span className="inline-flex items-center gap-1 tabular-nums">
                      <span className="uppercase tracking-wide text-[10px] text-muted-foreground/70">tokens</span>
                      <span className="font-mono">{formatNumber(r.usage.llm_total_tokens)}</span>
                    </span>
                    <span className="hidden sm:inline-flex items-center gap-1 tabular-nums text-muted-foreground/70">
                      ({formatNumber(r.usage.llm_prompt_tokens)} in · {formatNumber(r.usage.llm_completion_tokens)} out)
                    </span>
                  </>
                )}
              </div>
            </div>
            <div className="text-right text-[10px] text-muted-foreground font-mono tabular-nums whitespace-nowrap">
              {new Date(r.last_activity_at).toLocaleString()}
            </div>
          </div>
        </Card>
      ))}
    </div>
  );
}
