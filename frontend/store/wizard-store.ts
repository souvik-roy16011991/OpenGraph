"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

// Chat ("query") is no longer part of the build wizard — it lives as a
// standalone Playground route and has its own workspace selector. The union
// here is only the *build* steps that gate each other.
export type WizardStep = "upload" | "domain" | "graph-config" | "build" | "explore";

interface WizardState {
  completed: Record<WizardStep, boolean>;
  lastBuildJobId: string | null;
  highlightedNodeIds: string[];
  markCompleted: (step: WizardStep, value?: boolean) => void;
  setLastBuildJobId: (id: string | null) => void;
  setHighlightedNodeIds: (ids: string[]) => void;
  reset: () => void;
}

const initial: Record<WizardStep, boolean> = {
  upload: false,
  domain: false,
  "graph-config": false,
  build: false,
  explore: false,
};

export const useWizardStore = create<WizardState>()(
  persist(
    (set) => ({
      completed: initial,
      lastBuildJobId: null,
      highlightedNodeIds: [],
      markCompleted: (step, value = true) =>
        set((s) => ({ completed: { ...s.completed, [step]: value } })),
      setLastBuildJobId: (id) => set({ lastBuildJobId: id }),
      setHighlightedNodeIds: (ids) => set({ highlightedNodeIds: ids }),
      reset: () => set({ completed: initial, lastBuildJobId: null, highlightedNodeIds: [] }),
    }),
    { name: "kb-wizard" }
  )
);
