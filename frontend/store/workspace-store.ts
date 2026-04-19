"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface WorkspaceSummary {
  id: string;
  name: string;
  description?: string | null;
  created_at: string;
  updated_at: string;
  file_counts: { knowledge: number; tool: number };
  last_build_at?: string | null;
  last_build_status?: string | null;
  stats?: { total_nodes?: number; total_edges?: number } | null;
}

interface WorkspaceState {
  activeId: string | null;
  setActiveId: (id: string | null) => void;
  clear: () => void;
}

export const useWorkspaceStore = create<WorkspaceState>()(
  persist(
    (set) => ({
      activeId: null,
      setActiveId: (id) => set({ activeId: id }),
      clear: () => set({ activeId: null }),
    }),
    { name: "kb-active-workspace" }
  )
);

/** Read the active workspace id synchronously — only safe in browser. */
export function readActiveWorkspaceId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return useWorkspaceStore.getState().activeId;
  } catch {
    return null;
  }
}
