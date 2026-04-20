"use client";

import * as React from "react";
import Link from "next/link";
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
            <Briefcase className="h-5 w-5" /> Workspaces
          </h1>
          <p className="text-muted-foreground text-sm">
            Each workspace holds its own knowledge + tool understanding. Pick one to start,
            or create a new one.
          </p>
        </div>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogTrigger asChild>
            <Button size="lg"><Plus className="h-4 w-4" /> New workspace</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create workspace</DialogTitle>
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
            <Briefcase className="mx-auto h-8 w-8 text-muted-foreground opacity-50" />
            <div>
              <p className="font-medium">No workspaces yet</p>
              <p className="text-sm text-muted-foreground">Create one to start uploading KB files.</p>
            </div>
            <Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" /> New workspace</Button>
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
                router.push("/upload");
              }}
              onDelete={() => {
                if (confirm(`Delete workspace "${w.name}" and all its data?`)) {
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
  onDelete,
}: {
  ws: WorkspaceSummary;
  isActive: boolean;
  onOpen: () => void;
  onDelete: () => void;
}) {
  const status = ws.last_build_status;
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
        </div>
        <p className="text-[10px] text-muted-foreground font-mono">
          updated {new Date(ws.updated_at).toLocaleString()}
        </p>
        <div className="flex gap-2 pt-2">
          <Button size="sm" onClick={onOpen} className="flex-1">
            Open <ArrowRight className="h-3.5 w-3.5" />
          </Button>
          <Button size="sm" variant="outline" onClick={onDelete}>
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
