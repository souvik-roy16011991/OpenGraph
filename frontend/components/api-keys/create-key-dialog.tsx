"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Copy, Eye, EyeOff, KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import type { CreateApiKeyResponse } from "@/lib/schema";
import { cn } from "@/lib/utils";

type Props = {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  trigger?: React.ReactNode;
};

/**
 * Two-step dialog:
 *
 *   Step 1 — Form: name + optional workspace pin + rate limit override.
 *   Step 2 — Reveal: display the plaintext key ONCE with a copy button.
 *
 * The plaintext is only available in the POST response; if the user
 * closes the dialog without copying, the only recovery is revoking and
 * minting again.
 */
export function CreateKeyDialog({ open, onOpenChange, trigger }: Props) {
  const qc = useQueryClient();
  const [internalOpen, setInternalOpen] = React.useState(false);
  const isControlled = open !== undefined;
  const isOpen = isControlled ? open : internalOpen;
  const setOpen = React.useCallback(
    (next: boolean) => {
      if (!isControlled) setInternalOpen(next);
      onOpenChange?.(next);
    },
    [isControlled, onOpenChange],
  );

  const [name, setName] = React.useState("");
  const [workspaceId, setWorkspaceId] = React.useState<string>("");
  const [rateLimit, setRateLimit] = React.useState<string>("60");
  const [minted, setMinted] = React.useState<CreateApiKeyResponse | null>(null);
  const [revealed, setRevealed] = React.useState(false);
  const [copied, setCopied] = React.useState(false);

  const workspacesQuery = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.listWorkspaces,
    enabled: isOpen && minted === null,
  });

  const createMut = useMutation({
    mutationFn: () =>
      api.createApiKey({
        name: name.trim(),
        workspace_id: workspaceId || null,
        rate_limit_rpm: rateLimit ? parseInt(rateLimit, 10) || undefined : undefined,
      }),
    onSuccess: (res) => {
      setMinted(res);
      setRevealed(true);
      qc.invalidateQueries({ queryKey: ["api-keys"] });
      toast.success(`Key "${res.key.name}" created`);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const reset = () => {
    setName("");
    setWorkspaceId("");
    setRateLimit("60");
    setMinted(null);
    setRevealed(false);
    setCopied(false);
  };

  const onOpenChangeInternal = (v: boolean) => {
    setOpen(v);
    if (!v) setTimeout(reset, 200);
  };

  async function copyPlaintext() {
    if (!minted) return;
    try {
      await navigator.clipboard.writeText(minted.plaintext);
      setCopied(true);
      toast.success("Copied to clipboard");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Clipboard copy failed — select the text manually.");
    }
  }

  return (
    <Dialog open={isOpen} onOpenChange={onOpenChangeInternal}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
      <DialogContent className="max-w-md">
        {minted === null ? (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <KeyRound className="h-4 w-4" /> Create API key
              </DialogTitle>
              <DialogDescription>
                Each key carries its own rate limit and can be revoked independently.
                The full secret is shown exactly once on the next screen.
              </DialogDescription>
            </DialogHeader>
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                createMut.mutate();
              }}
            >
              <div className="space-y-1.5">
                <Label htmlFor="key-name">Name</Label>
                <Input
                  id="key-name"
                  autoFocus
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. production-backend"
                />
                <p className="text-[11px] text-muted-foreground">
                  A human-friendly label. Doesn&apos;t affect how the key works.
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="key-ws">Scope to a workspace (optional)</Label>
                <select
                  id="key-ws"
                  value={workspaceId}
                  onChange={(e) => setWorkspaceId(e.target.value)}
                  className="w-full h-9 rounded-md border bg-background px-3 text-sm"
                >
                  <option value="">Any workspace I own</option>
                  {(workspacesQuery.data?.workspaces ?? []).map((w) => (
                    <option key={w.id} value={w.id}>
                      {w.name}
                    </option>
                  ))}
                </select>
                <p className="text-[11px] text-muted-foreground">
                  Leaving this blank means the key can target any workspace you own,
                  with the caller passing <code>workspace_id</code> on each request.
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="key-rpm">Rate limit (requests per minute)</Label>
                <Input
                  id="key-rpm"
                  type="number"
                  min={1}
                  max={10000}
                  value={rateLimit}
                  onChange={(e) => setRateLimit(e.target.value)}
                />
              </div>
              <DialogFooter>
                <Button
                  variant="outline"
                  type="button"
                  onClick={() => onOpenChangeInternal(false)}
                  disabled={createMut.isPending}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={!name.trim() || createMut.isPending}>
                  {createMut.isPending ? "Creating…" : "Create key"}
                </Button>
              </DialogFooter>
            </form>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>Your new API key</DialogTitle>
              <DialogDescription>
                Copy it now — it won&apos;t be shown again. Store it somewhere safe
                (a password manager or your secret store).
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <Label className="text-xs uppercase tracking-wider text-muted-foreground">
                Secret
              </Label>
              <div className="flex items-stretch gap-2">
                <div
                  className={cn(
                    "flex-1 rounded-md border bg-muted/40 px-3 py-2 font-mono text-xs break-all",
                    !revealed && "select-none blur-[4px]",
                  )}
                >
                  {minted.plaintext}
                </div>
                <Button
                  variant="outline"
                  size="icon"
                  type="button"
                  onClick={() => setRevealed((r) => !r)}
                  aria-label={revealed ? "Hide" : "Reveal"}
                  title={revealed ? "Hide" : "Reveal"}
                >
                  {revealed ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  type="button"
                  onClick={copyPlaintext}
                  aria-label="Copy"
                  title="Copy"
                >
                  {copied ? (
                    <Check className="h-4 w-4 text-emerald-500" />
                  ) : (
                    <Copy className="h-4 w-4" />
                  )}
                </Button>
              </div>
              <p className="text-[11px] text-muted-foreground">
                Key id: <span className="font-mono">{minted.key.id}</span>
                {" · "}prefix <span className="font-mono">{minted.key.prefix}</span>
                {" · "}limit {minted.key.rate_limit_rpm} rpm
              </p>
            </div>
            <DialogFooter>
              <Button onClick={() => onOpenChangeInternal(false)}>Done</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
