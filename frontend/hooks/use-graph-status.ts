"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useWorkspaceStore } from "@/store/workspace-store";

export type GraphStatus =
  | { state: "ready"; nodes: number; edges: number }
  | { state: "empty" }
  | { state: "loading" }
  | { state: "no_workspace" }
  | { state: "error"; message: string };

export function useGraphStatus(): GraphStatus {
  const activeWs = useWorkspaceStore((s) => s.activeId);

  const { data, isLoading, error } = useQuery({
    queryKey: ["stats", activeWs],
    queryFn: api.stats,
    enabled: Boolean(activeWs),
    refetchInterval: 10_000,
  });

  if (!activeWs) return { state: "no_workspace" };
  if (isLoading) return { state: "loading" };
  if (error) {
    const msg = (error as Error).message;
    if (msg.toLowerCase().includes("no graph built") || msg.toLowerCase().includes("not loaded")) {
      return { state: "empty" };
    }
    return { state: "error", message: msg };
  }
  if (!data) return { state: "empty" };
  return { state: "ready", nodes: data.total_nodes, edges: data.total_edges };
}
