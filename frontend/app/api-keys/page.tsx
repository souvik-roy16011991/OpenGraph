"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  KeyRound,
  Plus,
  Trash2,
  AlertCircle,
  Briefcase,
  Clock,
  ExternalLink,
} from "lucide-react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { CreateKeyDialog } from "@/components/api-keys/create-key-dialog";
import { api } from "@/lib/api";
import type { ApiKeyRow } from "@/lib/schema";
import { cn } from "@/lib/utils";

export default function ApiKeysPage() {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = React.useState(false);
  const [toRevoke, setToRevoke] = React.useState<ApiKeyRow | null>(null);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["api-keys"],
    queryFn: api.listApiKeys,
    refetchInterval: 30_000,
  });

  const revokeMut = useMutation({
    mutationFn: (id: string) => api.revokeApiKey(id),
    onMutate: async (id: string) => {
      await qc.cancelQueries({ queryKey: ["api-keys"] });
      const prev = qc.getQueryData<ApiKeyRow[]>(["api-keys"]);
      if (prev) {
        qc.setQueryData<ApiKeyRow[]>(
          ["api-keys"],
          prev.map((k) =>
            k.id === id ? { ...k, revoked_at: new Date().toISOString() } : k,
          ),
        );
      }
      return { prev };
    },
    onError: (err: Error, _id, ctx) => {
      if (ctx?.prev) qc.setQueryData(["api-keys"], ctx.prev);
      toast.error(err.message);
    },
    onSuccess: () => toast.success("Key revoked"),
    onSettled: () => qc.invalidateQueries({ queryKey: ["api-keys"] }),
  });

  const keys = data ?? [];
  const live = keys.filter((k) => !k.revoked_at);
  const revoked = keys.filter((k) => k.revoked_at);

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <KeyRound className="h-5 w-5" /> API keys
          </h1>
          <p className="text-muted-foreground text-sm">
            Developer credentials for the public <code>/api/v1/ext/*</code> surface.
            Keys are shown once at creation; lost secrets can only be replaced by revoking and
            minting a new one.{" "}
            <Link href="/api-docs" className="underline hover:text-foreground">
              View the docs →
            </Link>
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)} size="lg" className="gap-2">
          <Plus className="h-4 w-4" /> New key
        </Button>
      </div>

      {isError && (
        <Card>
          <CardContent className="p-4 flex items-center gap-2 text-sm text-destructive">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {(error as Error).message}
          </CardContent>
        </Card>
      )}

      {isLoading ? (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="flex items-center gap-4 rounded-lg border bg-card px-4 py-3"
            >
              <Skeleton className="h-10 w-10 rounded-md" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-4 w-1/3" />
                <Skeleton className="h-3 w-2/3" />
              </div>
              <Skeleton className="h-8 w-20" />
            </div>
          ))}
        </div>
      ) : keys.length === 0 ? (
        <Card>
          <CardContent className="p-12 text-center space-y-4">
            <KeyRound className="mx-auto h-8 w-8 text-muted-foreground opacity-60" />
            <div>
              <p className="font-medium">No API keys yet</p>
              <p className="text-sm text-muted-foreground">
                Mint one to start calling the API from scripts, SDKs, or CI.
              </p>
            </div>
            <Button onClick={() => setCreateOpen(true)} className="gap-2">
              <Plus className="h-4 w-4" /> New key
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {live.map((k) => (
            <KeyRow key={k.id} k={k} onRevoke={() => setToRevoke(k)} />
          ))}
          {revoked.length > 0 && (
            <>
              <p className="pt-4 text-xs uppercase tracking-wider text-muted-foreground/70">
                Revoked
              </p>
              {revoked.map((k) => (
                <KeyRow key={k.id} k={k} onRevoke={null} />
              ))}
            </>
          )}
        </div>
      )}

      <CreateKeyDialog open={createOpen} onOpenChange={setCreateOpen} />

      <Dialog open={Boolean(toRevoke)} onOpenChange={(v) => !v && setToRevoke(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Revoke &quot;{toRevoke?.name}&quot;?</DialogTitle>
            <DialogDescription>
              Revocation is effective within ~5 minutes across all workers.
              In-flight requests using this key complete normally.
              You can&apos;t un-revoke — create a fresh key if you need to roll back.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setToRevoke(null)}
              disabled={revokeMut.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={revokeMut.isPending}
              onClick={() => {
                if (toRevoke) {
                  revokeMut.mutate(toRevoke.id);
                  setToRevoke(null);
                }
              }}
            >
              {revokeMut.isPending ? "Revoking…" : "Revoke key"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function KeyRow({
  k,
  onRevoke,
}: {
  k: ApiKeyRow;
  onRevoke: (() => void) | null;
}) {
  const isRevoked = Boolean(k.revoked_at);
  return (
    <div
      className={cn(
        "group flex items-center gap-4 rounded-lg border bg-card px-4 py-3 transition-colors",
        !isRevoked && "hover:border-foreground/20",
        isRevoked && "opacity-60",
      )}
    >
      <div className="h-10 w-10 rounded-md border bg-background flex items-center justify-center shrink-0">
        <KeyRound className="h-4 w-4 text-foreground/70" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <p className="font-medium text-sm truncate">{k.name}</p>
          <code className="text-[10px] font-mono text-muted-foreground/80">
            {k.prefix}…
          </code>
          {isRevoked && (
            <Badge variant="destructive" className="text-[10px] h-4 px-1.5">
              revoked
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground flex-wrap">
          {k.workspace_id ? (
            <span className="inline-flex items-center gap-1">
              <Briefcase className="h-3 w-3" />
              <span className="font-mono">{k.workspace_id.slice(0, 8)}…</span>
            </span>
          ) : (
            <span className="text-muted-foreground/80">account-scoped</span>
          )}
          <span>·</span>
          <span>{k.rate_limit_rpm} rpm</span>
          <span>·</span>
          <span className="inline-flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {k.last_used_at
              ? `last used ${relativeTime(k.last_used_at)}`
              : "never used"}
          </span>
        </div>
      </div>
      {onRevoke && (
        <Button
          variant="ghost"
          size="sm"
          onClick={onRevoke}
          className="gap-1.5 hover:text-destructive"
        >
          <Trash2 className="h-3.5 w-3.5" />
          Revoke
        </Button>
      )}
    </div>
  );
}

function relativeTime(iso: string): string {
  const t = new Date(iso).getTime();
  const diff = Date.now() - t;
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}
