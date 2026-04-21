"use client";

/**
 * Central per-user state reset.
 *
 * Multi-tenant isolation on this app is enforced server-side (every backend
 * route filters by the JWT's user id), but the browser keeps a few pieces
 * of state that would otherwise bleed across accounts on the same device:
 *
 *   - Zustand-persisted active workspace / chat workspace / wizard progress
 *   - Per-workspace chat session ids (`kb.chatSessionId.<workspace_id>`)
 *   - Tanstack-query result cache (/api/v1/me, /workspaces, etc.)
 *
 * Call `resetLocalUserState()` on sign-out and on every detected user
 * switch. Sidebar collapse is a UI preference — we deliberately keep it.
 */

import type { QueryClient } from "@tanstack/react-query";
import { useChatStore } from "@/store/chat-store";
import { useWizardStore } from "@/store/wizard-store";
import { useWorkspaceStore } from "@/store/workspace-store";

const ZUSTAND_KEYS_TO_WIPE = [
  "kb-active-workspace",
  "kb-chat-workspace",
  "kb-wizard",
];

export function resetLocalUserState(queryClient?: QueryClient): void {
  try {
    useWorkspaceStore.getState().clear();
  } catch {}
  try {
    useChatStore.setState({ chatActiveId: null });
  } catch {}
  try {
    useWizardStore.getState().reset();
  } catch {}

  if (typeof window !== "undefined") {
    try {
      ZUSTAND_KEYS_TO_WIPE.forEach((k) => window.localStorage.removeItem(k));
      // Per-workspace chat-session ids: `kb.chatSessionId.<uuid>`
      const toDrop: string[] = [];
      for (let i = 0; i < window.localStorage.length; i++) {
        const k = window.localStorage.key(i);
        if (k && k.startsWith("kb.chatSessionId.")) toDrop.push(k);
      }
      toDrop.forEach((k) => window.localStorage.removeItem(k));
    } catch {}
  }

  if (queryClient) {
    try {
      queryClient.clear();
    } catch {}
  }
}
