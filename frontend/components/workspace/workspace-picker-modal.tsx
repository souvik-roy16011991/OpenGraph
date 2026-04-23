"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowRight,
  Briefcase,
  CheckCircle2,
  GitBranch,
  LayoutGrid,
  Plus,
  Search,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { CreateWorkspaceDialog } from "@/components/workspace/create-workspace-dialog";
import { api } from "@/lib/api";
import type { WorkspaceSummary } from "@/lib/schema";
import { useWorkspaceStore } from "@/store/workspace-store";
import { cn } from "@/lib/utils";

/**
 * Inline gate that asks the user to pick a workspace before a workspace-
 * scoped route can render anything useful.
 *
 * Invariant: if this modal is mounted, ``activeId`` is ``null``. The
 * AppShell decides when to mount it based on the current pathname; this
 * component's job is only to land the user on a workspace quickly.
 *
 * Behaviour:
 *   - Loading → skeleton rows.
 *   - Exactly one live workspace → auto-select, no user action. The
 *     modal dismounts as soon as ``activeId`` is set.
 *   - 2+ workspaces → search + row list, click to activate.
 *   - Zero workspaces → empty state with two clear next actions:
 *     Browse templates (/templates) or Create workspace (inline dialog).
 *   - Not dismissible. No Cancel, no X, no outside-click. The user is
 *     on a page that needs a workspace; blocking + guiding is clearer
 *     than a soft hint + blank content.
 */
export function WorkspacePickerModal() {
  const router = useRouter();
  const setActiveId = useWorkspaceStore((s) => s.setActiveId);
  const [query, setQuery] = React.useState("");
  const [createOpen, setCreateOpen] = React.useState(false);

  const q = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.listWorkspaces,
  });

  // Live = not soft-deleted. The backend already filters, but we belt-
  // and-suspenders here so a cache glitch doesn't surface a tombstone.
  const workspaces = React.useMemo(
    () => (q.data?.workspaces ?? []).filter((w) => !(w as WorkspaceSummary & { deleted_at?: string | null }).deleted_at),
    [q.data],
  );

  // Auto-select a singleton. Runs once the first successful fetch resolves;
  // the store update triggers the gate to unmount this component.
  React.useEffect(() => {
    if (q.isLoading) return;
    if (workspaces.length === 1) {
      setActiveId(workspaces[0].id);
    }
  }, [q.isLoading, workspaces, setActiveId]);

  const filtered = React.useMemo(() => {
    const trimmed = query.trim().toLowerCase();
    if (!trimmed) return workspaces;
    return workspaces.filter(
      (w) =>
        w.name.toLowerCase().includes(trimmed) ||
        (w.description ?? "").toLowerCase().includes(trimmed),
    );
  }, [workspaces, query]);

  const onPick = (w: WorkspaceSummary) => {
    setActiveId(w.id);
  };

  // Workaround for not-dismissible Dialog: override onOpenChange so Radix's
  // internal close attempts (Escape, overlay click, programmatic close) are
  // ignored while activeId is still null.
  return (
    <>
      <Dialog open onOpenChange={() => { /* intentionally uncloseable */ }}>
        <DialogContent
          className="max-w-lg"
          // Block Radix's default dismiss behaviours so the user can't
          // escape the gate without picking something.
          onPointerDownOutside={(e) => e.preventDefault()}
          onEscapeKeyDown={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <GitBranch className="h-4 w-4" />
              Choose a workspace
            </DialogTitle>
            <DialogDescription>
              This page works per-workspace. Pick one to continue — or head
              back to <span className="font-medium text-foreground">My Graphs</span>{" "}
              from the sidebar if you&apos;d rather manage them first.
            </DialogDescription>
          </DialogHeader>

          {q.isLoading ? (
            <SkeletonList />
          ) : workspaces.length === 0 ? (
            <EmptyState
              onBrowseTemplates={() => router.push("/templates")}
              onCreate={() => setCreateOpen(true)}
            />
          ) : (
            <>
              {workspaces.length >= 2 && (
                <div className="relative">
                  <Search className="h-4 w-4 absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none" />
                  <Input
                    autoFocus
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={`Search ${workspaces.length} workspaces…`}
                    className="pl-9"
                  />
                </div>
              )}

              <div className="max-h-[320px] overflow-y-auto -mx-6 px-6">
                {filtered.length === 0 ? (
                  <div className="rounded-lg border border-dashed bg-card/40 px-4 py-6 text-center">
                    <p className="text-sm text-muted-foreground">
                      No workspace matches &ldquo;{query}&rdquo;.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {filtered.map((w) => (
                      <Row key={w.id} ws={w} onPick={() => onPick(w)} />
                    ))}
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between pt-2 border-t">
                <p className="text-[11px] text-muted-foreground">
                  {workspaces.length} workspace{workspaces.length === 1 ? "" : "s"}
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setCreateOpen(true)}
                  className="gap-1.5"
                >
                  <Plus className="h-3.5 w-3.5" /> New workspace
                </Button>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Inline create dialog — minting a workspace sets it active via the
       *  existing CreateWorkspaceDialog side-effects and this gate
       *  auto-unmounts on the next render. */}
      <CreateWorkspaceDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={() => {
          // The CreateWorkspaceDialog already set activeId. Close both
          // dialogs; the outer picker un-mounts on the next pathname /
          // activeId tick.
          setCreateOpen(false);
        }}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Row — same pattern as the template list so visuals stay consistent.
// ---------------------------------------------------------------------------

function Row({ ws, onPick }: { ws: WorkspaceSummary; onPick: () => void }) {
  const status = ws.last_build_status;
  const isDeployed = Boolean((ws as WorkspaceSummary & { deployed_at?: string | null }).deployed_at);
  const dotCls =
    status === "error"
      ? "bg-destructive"
      : isDeployed
        ? "bg-emerald-500"
        : status === "done"
          ? "bg-emerald-500/40"
          : status === "queued" || status === "running"
            ? "bg-sky-500 animate-pulse"
            : "bg-muted-foreground/40";
  const fileCount = ws.file_counts.knowledge + ws.file_counts.tool;

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={onPick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onPick();
        }
      }}
      className={cn(
        "group flex items-center gap-3 rounded-lg border bg-card px-3 py-2.5 cursor-pointer",
        "transition-colors hover:border-foreground/25 hover:bg-accent/30",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      )}
    >
      <div className="h-9 w-9 rounded-md border bg-background flex items-center justify-center shrink-0">
        <Briefcase className="h-4 w-4 text-foreground/70" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span
            aria-hidden
            className={cn("h-2 w-2 rounded-full shrink-0", dotCls)}
          />
          <p className="font-medium text-sm truncate">{ws.name}</p>
          {isDeployed && (
            <Badge variant="secondary" className="gap-1 text-[10px] h-4 px-1.5">
              <CheckCircle2 className="h-3 w-3 text-emerald-500" />
              deployed
            </Badge>
          )}
        </div>
        <p className="text-[11px] text-muted-foreground truncate mt-0.5">
          {fileCount > 0 ? `${fileCount} file${fileCount === 1 ? "" : "s"}` : "No files"}
          {ws.stats?.total_nodes !== undefined && ` · ${ws.stats.total_nodes} nodes`}
          {" · updated "}
          {relativeTime(ws.updated_at)}
        </p>
      </div>
      <ArrowRight className="h-3.5 w-3.5 text-muted-foreground shrink-0 transition-transform group-hover:translate-x-0.5" />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state — zero workspaces
// ---------------------------------------------------------------------------

function EmptyState({
  onBrowseTemplates,
  onCreate,
}: {
  onBrowseTemplates: () => void;
  onCreate: () => void;
}) {
  return (
    <div className="rounded-lg border border-dashed bg-card/40 px-6 py-8 text-center space-y-4">
      <GitBranch className="mx-auto h-8 w-8 text-muted-foreground/70" />
      <div>
        <p className="font-medium">No workspaces yet</p>
        <p className="text-sm text-muted-foreground mt-0.5">
          Start from a template or create an empty workspace.
        </p>
      </div>
      <div className="flex items-center justify-center gap-2 flex-wrap">
        <Button variant="outline" size="sm" onClick={onBrowseTemplates} className="gap-1.5">
          <LayoutGrid className="h-3.5 w-3.5" /> Browse templates
        </Button>
        <Button size="sm" onClick={onCreate} className="gap-1.5">
          <Plus className="h-3.5 w-3.5" /> Create workspace
        </Button>
      </div>
    </div>
  );
}

function SkeletonList() {
  return (
    <div className="space-y-2">
      {[0, 1, 2].map((i) => (
        <div key={i} className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2.5">
          <Skeleton className="h-9 w-9 rounded-md" />
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-1/2" />
            <Skeleton className="h-3 w-2/3" />
          </div>
          <Skeleton className="h-3 w-3" />
        </div>
      ))}
    </div>
  );
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return "just now";
  const m = Math.floor(diff / 60_000);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d}d ago`;
  return new Date(iso).toLocaleDateString();
}
