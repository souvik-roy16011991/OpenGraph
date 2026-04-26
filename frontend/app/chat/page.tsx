"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { MessageSquareText } from "lucide-react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Chat } from "@/components/query/chat";
import { Button } from "@/components/ui/button";
import { useChatStore } from "@/store/chat-store";

/**
 * Standalone Playground — pick any workspace you own and chat with its
 * built knowledge graph. The chat workspace is tracked in its OWN zustand
 * slice (`chat-store`) so it doesn't interfere with the wizard sidebar's
 * active workspace: you can chat with workspace A while still tuning
 * workspace B in the build flow.
 */
export default function ChatPage() {
  const chatActiveId = useChatStore((s) => s.chatActiveId);
  const setChatActiveId = useChatStore((s) => s.setChatActiveId);

  const wsQuery = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.listWorkspaces,
    staleTime: 15_000,
  });

  const workspaces = wsQuery.data?.workspaces ?? [];

  // Auto-select the most recent workspace on first visit so the user can
  // start chatting immediately instead of facing an empty state.
  React.useEffect(() => {
    if (!chatActiveId && workspaces.length > 0) {
      setChatActiveId(workspaces[0].id);
    }
  }, [chatActiveId, workspaces, setChatActiveId]);

  // If the selected workspace was deleted, drop the stale selection.
  React.useEffect(() => {
    if (chatActiveId && workspaces.length > 0 && !workspaces.some((w) => w.id === chatActiveId)) {
      setChatActiveId(workspaces.length > 0 ? workspaces[0].id : null);
    }
  }, [chatActiveId, workspaces, setChatActiveId]);

  if (wsQuery.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  if (workspaces.length === 0) {
    return (
      <div className="mx-auto max-w-md text-center space-y-4 py-12">
        <MessageSquareText className="mx-auto h-10 w-10 text-muted-foreground" />
        <h2 className="text-lg font-medium">No workspaces yet</h2>
        <p className="text-sm text-muted-foreground">
          Create one from a template to start chatting with your knowledge graph.
        </p>
        <Button asChild>
          <Link href="/templates">Browse templates</Link>
        </Button>
      </div>
    );
  }

  const selected = workspaces.find((w) => w.id === chatActiveId) ?? null;

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Chat</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Chat with a built knowledge graph. Choose a workspace, pick a model,
            and watch the agent reason through each step.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <label className="text-[10px] uppercase tracking-wider text-muted-foreground font-mono">
            Chat workspace
          </label>
          <select
            value={chatActiveId ?? ""}
            onChange={(e) => setChatActiveId(e.target.value || null)}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm min-w-[240px]"
          >
            {workspaces.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name}
                {w.last_build_status === "done" ? "" : "  (no build)"}
              </option>
            ))}
          </select>
          {selected && selected.last_build_status !== "done" ? (
            <span className="text-[10px] text-amber-500 font-mono">
              This workspace has no successful build yet — chat will return 503.
            </span>
          ) : null}
        </div>
      </div>

      <Chat workspaceId={chatActiveId} />
    </div>
  );
}
