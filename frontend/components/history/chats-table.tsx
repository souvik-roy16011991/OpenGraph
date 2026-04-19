"use client";

import { useQuery } from "@tanstack/react-query";
import { MessageSquare } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";

export function ChatsTable() {
  const q = useQuery({ queryKey: ["history", "chats"], queryFn: () => api.historyChats(100), refetchInterval: 5000 });
  if (q.isLoading) return <p className="text-sm text-muted-foreground">Loading chat history…</p>;
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
              <div className="flex items-center gap-2 mt-1 flex-wrap">
                <Badge variant="outline" className="text-[10px] font-mono">{r.session_id.slice(0, 8)}…</Badge>
                <span className="text-xs text-muted-foreground">{r.message_count} messages</span>
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
