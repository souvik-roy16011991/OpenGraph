"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Plus,
  Briefcase,
  ArrowRight,
  Trash2,
  FileJson,
  Network,
  CheckCircle2,
  AlertCircle,
  Pencil,
  Play,
  Loader2,
  GitBranch,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
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
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");

  const createMut = useMutation({
    mutationFn: () => api.createWorkspace({ name, description: description || undefined }),
    onSuccess: (ws) => {
      toast.success(`Workspace "${ws.name}" created`);
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      setActiveId(ws.id);
      setOpen(false);
      setName("");
      setDescription("");
      setTimeout(() => router.push("/upload"), 300);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => api.deleteWorkspace(id),
    onSuccess: (_, id) => {
      toast.success("Workspace deleted");
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      if (id === activeId) setActiveId(null);
    },
    onError: (err: Error) => toast.error(err.message),
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
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button size="lg"><Plus className="h-4 w-4" /> New graph</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create graph</DialogTitle>
              <DialogDescription>
                Name it after the domain it will hold (e.g. &quot;Loan Assessment&quot;). You can rename it later.
              </DialogDescription>
            </DialogHeader>
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                createMut.mutate();
              }}
            >
              <div className="space-y-2">
                <Label>Name</Label>
                <Input
                  autoFocus
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Loan assessment"
                />
              </div>
              <div className="space-y-2">
                <Label>Description (optional)</Label>
                <Textarea
                  rows={3}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What does this workspace cover?"
                />
              </div>
              <Button type="submit" disabled={!name.trim() || createMut.isPending}>
                {createMut.isPending ? "Creating…" : "Create & start uploading"}
              </Button>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      {isLoading && <p className="text-sm text-muted-foreground">Loading workspaces…</p>}
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
              onDelete={() => {
                if (confirm(`Delete graph "${w.name}" and all its data?`)) {
                  deleteMut.mutate(w.id);
                }
              }}
            />
          ))}
        </div>
      )}
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
}: {
  ws: WorkspaceSummary;
  isActive: boolean;
  onOpen: () => void;
  onEdit: () => void;
  onRerun: () => void;
  onDelete: () => void;
}) {
  const status = ws.last_build_status;
  const isBuilding = status === "queued" || status === "running";
  const hasBuild = status === "done";
  const openLabel = hasBuild ? "Explore" : "Open";
  return (
    <Card className={isActive ? "border-primary/60 shadow-md" : undefined}>
      <CardHeader>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="text-base truncate">{ws.name}</CardTitle>
            {ws.description && (
              <CardDescription className="line-clamp-2">{ws.description}</CardDescription>
            )}
          </div>
          {isActive && (
            <Badge variant="success" className="text-[10px]">active</Badge>
          )}
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
