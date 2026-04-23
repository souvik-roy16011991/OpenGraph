"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Plus,
  ArrowRight,
  Trash2,
  FileJson,
  Network,
  CheckCircle2,
  AlertCircle,
  Pencil,
  Play,
  Loader2,
  Rocket,
  GitBranch,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { CreateWorkspaceDialog } from "@/components/workspace/create-workspace-dialog";
import { DeployDialog, DeployedBadge } from "@/components/workspace/deploy-dialog";
import { api } from "@/lib/api";
import type { WorkspaceSummary } from "@/lib/schema";
import { useWorkspaceStore } from "@/store/workspace-store";

export default function WorkspacesPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const setActiveId = useWorkspaceStore((s) => s.setActiveId);
  const activeId = useWorkspaceStore((s) => s.activeId);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.listWorkspaces,
    refetchInterval: 10_000,
  });

  const [open, setOpen] = React.useState(false);
  const [toDelete, setToDelete] = React.useState<WorkspaceSummary | null>(null);
  const [toDeploy, setToDeploy] = React.useState<WorkspaceSummary | null>(null);

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.deleteWorkspace(id),
    onMutate: async (id: string) => {
      await qc.cancelQueries({ queryKey: ["workspaces"] });
      const prev = qc.getQueryData<{ workspaces: WorkspaceSummary[] }>(["workspaces"]);
      if (prev) {
        qc.setQueryData<{ workspaces: WorkspaceSummary[] }>(["workspaces"], {
          workspaces: prev.workspaces.filter((w) => w.id !== id),
        });
      }
      if (id === activeId) setActiveId(null);
      return { prev };
    },
    onError: (err: Error, _id, ctx) => {
      if (ctx?.prev) qc.setQueryData(["workspaces"], ctx.prev);
      toast.error(err.message);
    },
    onSuccess: () => {
      toast.success("Workspace deleted");
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["workspaces"] });
    },
  });

  const rows = data?.workspaces ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <GitBranch className="h-5 w-5" /> My graphs
          </h1>
          <p className="text-muted-foreground text-sm">
            Every graph has its own files, domain profile, and build history.
            Open one to explore or chat, edit it to change the inputs, or
            rerun the build to pick up new changes.
          </p>
        </div>
        <CreateWorkspaceDialog
          open={open}
          onOpenChange={setOpen}
          trigger={
            <Button size="lg"><Plus className="h-4 w-4" /> New graph</Button>
          }
        />
      </div>

      {isLoading && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Card key={i}>
              <CardHeader>
                <Skeleton className="h-5 w-3/5" />
                <Skeleton className="h-3 w-4/5 mt-2" />
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="flex gap-1.5">
                  <Skeleton className="h-5 w-16 rounded-full" />
                  <Skeleton className="h-5 w-20 rounded-full" />
                </div>
                <Skeleton className="h-3 w-32" />
                <div className="flex gap-1.5 pt-2">
                  <Skeleton className="h-8 flex-1" />
                  <Skeleton className="h-8 w-16" />
                  <Skeleton className="h-8 w-16" />
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      {isError && <p className="text-sm text-destructive">{(error as Error).message}</p>}

      {!isLoading && rows.length === 0 && (
        <Card>
          <CardContent className="p-12 text-center space-y-4">
            <GitBranch className="mx-auto h-8 w-8 text-muted-foreground opacity-50" />
            <div>
              <p className="font-medium">No graphs yet</p>
              <p className="text-sm text-muted-foreground">Create one to start uploading KB files, or pick a template.</p>
            </div>
            <div className="flex justify-center gap-2">
              <Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" /> New graph</Button>
              <Button variant="outline" onClick={() => router.push("/templates")}>Browse templates</Button>
            </div>
          </CardContent>
        </Card>
      )}

      {rows.length > 0 && (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {rows.map((w) => (
            <WorkspaceCard
              key={w.id}
              ws={w}
              isActive={activeId === w.id}
              onOpen={() => {
                setActiveId(w.id);
                // Built graphs → Explore. Un-built → Upload (wizard entry).
                router.push(w.last_build_status === "done" ? "/explore" : "/upload");
              }}
              onEdit={() => {
                setActiveId(w.id);
                router.push("/upload");
              }}
              onRerun={() => {
                setActiveId(w.id);
                // ?autostart=1 makes the build page fire the pipeline on
                // mount instead of requiring another click.
                router.push("/build?autostart=1");
              }}
              onDelete={() => setToDelete(w)}
              onDeploy={() => setToDeploy(w)}
            />
          ))}
        </div>
      )}

      <DeployDialog
        workspace={toDeploy}
        open={toDeploy !== null}
        onOpenChange={(o) => !o && setToDeploy(null)}
      />

      <Dialog open={toDelete !== null} onOpenChange={(o) => !o && setToDelete(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete &quot;{toDelete?.name}&quot;?</DialogTitle>
            <DialogDescription>
              This permanently removes the graph, all uploaded files, vectors, and build history.
              This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          {toDelete && (
            <div className="rounded-md border bg-muted/40 p-3 text-xs space-y-1">
              <div className="flex justify-between">
                <span className="text-muted-foreground">Knowledge files</span>
                <span className="font-mono">{toDelete.file_counts.knowledge}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-muted-foreground">Tool files</span>
                <span className="font-mono">{toDelete.file_counts.tool}</span>
              </div>
              {toDelete.stats?.total_nodes !== undefined && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Graph nodes</span>
                  <span className="font-mono">{toDelete.stats.total_nodes}</span>
                </div>
              )}
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setToDelete(null)} disabled={deleteMut.isPending}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteMut.isPending}
              onClick={() => {
                if (toDelete) {
                  deleteMut.mutate(toDelete.id);
                  setToDelete(null);
                }
              }}
            >
              {deleteMut.isPending ? "Deleting…" : "Delete permanently"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function WorkspaceCard({
  ws,
  isActive,
  onOpen,
  onEdit,
  onRerun,
  onDelete,
  onDeploy,
}: {
  ws: WorkspaceSummary;
  isActive: boolean;
  onOpen: () => void;
  onEdit: () => void;
  onRerun: () => void;
  onDelete: () => void;
  onDeploy: () => void;
}) {
  const status = ws.last_build_status;
  const isBuilding = status === "queued" || status === "running";
  const hasBuild = status === "done";
  const isDeployed = Boolean(ws.deployed_at);
  const openLabel = hasBuild ? "Explore" : "Open";

  // Button label + tooltip reflect the three possible deploy states so the
  // user understands what happens on click without opening the dialog.
  const deployTitle = !hasBuild
    ? "Build your graph first — Deploy needs a successful build"
    : isDeployed
      ? "Mint a fresh API key and restamp this graph as live"
      : "Publish this graph to /api/v1/ext/* and mint an API key";
  const deployLabel = isDeployed ? "Redeploy" : "Deploy";

  return (
    <Card className={isActive ? "border-primary/60 shadow-md" : undefined}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <CardTitle className="text-base truncate">{ws.name}</CardTitle>
              {isActive && (
                <Badge variant="success" className="text-[10px]">active</Badge>
              )}
              {isDeployed && <DeployedBadge />}
            </div>
            {ws.description && (
              <CardDescription className="line-clamp-2 mt-1">{ws.description}</CardDescription>
            )}
          </div>
          <Button
            size="sm"
            variant={isDeployed ? "outline" : "default"}
            onClick={onDeploy}
            disabled={!hasBuild}
            title={deployTitle}
            className="gap-1.5 shrink-0"
          >
            <Rocket className="h-3.5 w-3.5" />
            {deployLabel}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap gap-1.5 text-[11px]">
          <Badge variant="secondary" className="gap-1">
            <FileJson className="h-3 w-3" /> k{ws.file_counts.knowledge} · t{ws.file_counts.tool}
          </Badge>
          {ws.stats?.total_nodes !== undefined && (
            <Badge variant="outline" className="gap-1">
              <Network className="h-3 w-3" /> {ws.stats.total_nodes} nodes
            </Badge>
          )}
          {status === "done" && (
            <Badge variant="success" className="gap-1"><CheckCircle2 className="h-3 w-3" /> built</Badge>
          )}
          {status === "error" && (
            <Badge variant="destructive" className="gap-1"><AlertCircle className="h-3 w-3" /> build failed</Badge>
          )}
          {isBuilding && (
            <Badge variant="outline" className="gap-1">
              <Loader2 className="h-3 w-3 animate-spin" /> building
            </Badge>
          )}
        </div>
        <p className="text-[10px] text-muted-foreground font-mono">
          updated {new Date(ws.updated_at).toLocaleString()}
        </p>
        <div className="flex items-center gap-1.5 pt-2">
          <Button size="sm" onClick={onOpen} className="flex-1 gap-1.5">
            {openLabel} <ArrowRight className="h-3.5 w-3.5" />
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={onEdit}
            className="gap-1.5"
            title="Edit files, domain, or graph config"
            disabled={isBuilding}
          >
            <Pencil className="h-3.5 w-3.5" />
            Edit
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={onRerun}
            className="gap-1.5"
            title={isBuilding ? "Build already running" : "Run the build pipeline"}
            disabled={isBuilding}
          >
            {isBuilding ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            {isBuilding ? "Running…" : "Rerun"}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={onDelete}
            aria-label="Delete graph"
            className="hover:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
