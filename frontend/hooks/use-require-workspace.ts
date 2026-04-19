"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useWorkspaceStore } from "@/store/workspace-store";

/**
 * Bounces to /workspaces if no workspace is active.
 * Returns the active id (or null while the redirect is in flight).
 */
export function useRequireWorkspace(): string | null {
  const router = useRouter();
  const activeId = useWorkspaceStore((s) => s.activeId);

  React.useEffect(() => {
    if (!activeId) router.replace("/workspaces");
  }, [activeId, router]);

  return activeId;
}
