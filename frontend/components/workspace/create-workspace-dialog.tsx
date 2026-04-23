"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import type { WorkspaceSummary } from "@/lib/schema";
import { useWorkspaceStore } from "@/store/workspace-store";

type CreateWorkspaceDialogProps = {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  trigger?: React.ReactNode;
  onCreated?: (ws: WorkspaceSummary) => void;
};

export function CreateWorkspaceDialog({
  open: controlledOpen,
  onOpenChange,
  trigger,
  onCreated,
}: CreateWorkspaceDialogProps) {
  const router = useRouter();
  const qc = useQueryClient();
  const setActiveId = useWorkspaceStore((s) => s.setActiveId);

  const [internalOpen, setInternalOpen] = React.useState(false);
  const isControlled = controlledOpen !== undefined;
  const open = isControlled ? controlledOpen : internalOpen;
  const setOpen = React.useCallback(
    (next: boolean) => {
      if (!isControlled) setInternalOpen(next);
      onOpenChange?.(next);
    },
    [isControlled, onOpenChange],
  );

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
      if (onCreated) {
        onCreated(ws);
      } else {
        setTimeout(() => router.push("/upload"), 300);
      }
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
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
  );
}
