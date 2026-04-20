"use client";

import { create } from "zustand";
import { persist } from "zustand/middleware";

/**
 * Chat-specific active workspace id.
 *
 * Kept separate from ``workspace-store`` so a user can chat with workspace A
 * in the Playground while still building workspace B in the wizard sidebar.
 * Per-workspace chat session ids continue to live under
 * ``kb.chatSessionId.{workspace_id}`` in localStorage as they did before.
 */

interface ChatState {
  chatActiveId: string | null;
  setChatActiveId: (id: string | null) => void;
}

export const useChatStore = create<ChatState>()(
  persist(
    (set) => ({
      chatActiveId: null,
      setChatActiveId: (id) => set({ chatActiveId: id }),
    }),
    { name: "kb-chat-workspace" },
  ),
);
