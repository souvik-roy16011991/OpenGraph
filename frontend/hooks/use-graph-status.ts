"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export type GraphStatus =
  | { state: "ready"; nodes: number; edges: number }
  | { state: "empty" }
  | { state: "loading" }
  | { state: "error"; message: string };

export function useGraphStatus(): GraphStatus {
  const { data, isLoading, error } = useQuery({
    queryKey: ["stats"],
    queryFn: api.stats,
    refetchInterval: 10_000,
  });
  if (isLoading) return { state: "loading" };
  if (error) {
    const msg = (error as Error).message;
    if (msg.toLowerCase().includes("not loaded")) return { state: "empty" };
    return { state: "error", message: msg };
  }
  if (!data) return { state: "empty" };
  return { state: "ready", nodes: data.total_nodes, edges: data.total_edges };
}
