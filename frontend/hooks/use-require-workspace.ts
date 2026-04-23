"use client";

import { useWorkspaceStore } from "@/store/workspace-store";

/**
 * Subscribe to the active workspace id.
 *
 * When no workspace is selected the AppShell overlays a
 * :class:`WorkspacePickerModal` for every route listed in
 * ``WORKSPACE_SCOPED_ROUTES``, so individual pages don't need to
 * redirect — they just render ``null`` (or a skeleton) and wait for
 * ``activeId`` to flip. The modal auto-selects when only one workspace
 * exists, so the typical case is a single render pass with the hook
 * returning a real id.
 *
 * Usage:
 *
 *     const activeWs = useRequireWorkspace();
 *     if (!activeWs) return null;   // picker modal is showing on top
 *
 * Historical: this hook used to ``router.replace("/workspaces")`` on
 * a null, which yanked the user away from their intended destination
 * and flashed a different page. The picker modal replaces that.
 */
export function useRequireWorkspace(): string | null {
  return useWorkspaceStore((s) => s.activeId);
}
