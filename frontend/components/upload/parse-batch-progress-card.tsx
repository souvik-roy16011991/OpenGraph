"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  FileText,
  Loader2,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import type { ParseJob } from "@/lib/schema";
import { cn, formatBytes } from "@/lib/utils";

/**
 * Aggregate progress card for large parse batches.
 *
 * Polls the batched ``GET /api/v1/kb/parse-jobs`` endpoint — one request
 * regardless of how many jobs the user has in flight. That keeps
 * backend traffic flat at O(1) even when 200 files are being parsed.
 *
 * Used by the upload page when the in-flight job count is ≥ 10; below
 * that threshold the per-job ``ParseProgressCard`` reads nicer.
 */
export function ParseBatchProgressCard({
  jobIds,
  workspaceId,
  onDismissAll,
}: {
  jobIds: string[];  // the jobs this card is tracking — filters server response to avoid showing old history
  workspaceId: string | null;
  onDismissAll: () => void;
}) {
  const qc = useQueryClient();
  const [expanded, setExpanded] = React.useState(false);

  // One query, one endpoint, cheap to keep warm while the batch runs.
  // We filter client-side to this card's `jobIds` so opening /upload
  // doesn't resurrect unrelated jobs from an earlier session.
  const q = useQuery({
    queryKey: ["parse-jobs-batch", workspaceId, jobIds.join(",")],
    queryFn: () =>
      api.listParseJobs({
        workspace_id: workspaceId ?? undefined,
        // Include every status — the card transitions from queued/running
        // to done/error and we want to surface the final rollup too.
        status: ["queued", "running", "done", "error", "cancelled"],
        limit: 500,
      }),
    // Poll every 3 s while anything is still in flight; stop when the
    // batch is fully settled.
    refetchInterval: (query) => {
      const rows = (query.state.data?.jobs ?? []).filter((j) => jobIds.includes(j.job_id));
      const active = rows.some((j) => j.status === "queued" || j.status === "running");
      return active ? 3000 : false;
    },
    refetchIntervalInBackground: true,
    staleTime: 0,
    enabled: jobIds.length > 0,
  });

  const rows: ParseJob[] = React.useMemo(
    () => (q.data?.jobs ?? []).filter((j) => jobIds.includes(j.job_id)),
    [q.data, jobIds],
  );

  const counts = React.useMemo(() => {
    const c = { queued: 0, running: 0, done: 0, error: 0, cancelled: 0 };
    for (const j of rows) c[j.status]++;
    return c;
  }, [rows]);

  const total = jobIds.length;
  const settled = counts.done + counts.error + counts.cancelled;
  const active = counts.queued + counts.running;
  const percent = total > 0 ? Math.round((settled / total) * 100) : 0;
  const allDone = active === 0 && total > 0;

  // Once the whole batch settles, nudge the workspace-files list so the
  // new rows show up without a manual refresh.
  const seenAllDoneRef = React.useRef(false);
  React.useEffect(() => {
    if (allDone && !seenAllDoneRef.current && workspaceId) {
      seenAllDoneRef.current = true;
      qc.invalidateQueries({ queryKey: ["workspace-files", workspaceId] });
      qc.invalidateQueries({ queryKey: ["workspaces"] });
    }
  }, [allDone, workspaceId, qc]);

  const headerIcon = allDone
    ? counts.error > 0
      ? <AlertTriangle className="h-4 w-4 text-amber-500" />
      : <CheckCircle2 className="h-4 w-4 text-emerald-500" />
    : <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />;

  return (
    <div
      className={cn(
        "rounded-md border bg-card",
        allDone && counts.error === 0 && "border-emerald-500/40 bg-emerald-500/5",
        counts.error > 0 && !active && "border-amber-500/40 bg-amber-500/5",
      )}
    >
      <div className="flex items-start gap-3 px-3 py-2.5 text-sm">
        <div className="mt-0.5 shrink-0">{headerIcon}</div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 min-w-0">
            <p className="font-medium truncate">
              {allDone
                ? `${counts.done} of ${total} parsed`
                : `Parsing ${settled}/${total} documents`}
            </p>
          </div>

          <p className="text-[11px] text-muted-foreground mt-0.5">
            {counts.running > 0 && <>{counts.running} running</>}
            {counts.queued > 0 && <>{counts.running > 0 ? " · " : ""}{counts.queued} queued</>}
            {counts.done > 0 && ((counts.running || counts.queued) ? " · " : "") + `${counts.done} done`}
            {counts.error > 0 && <> · <span className="text-destructive">{counts.error} failed</span></>}
            {counts.cancelled > 0 && ` · ${counts.cancelled} cancelled`}
          </p>

          {active > 0 && (
            <div className="mt-2 h-1 w-full rounded bg-border/70 overflow-hidden">
              <div
                className="h-full bg-sky-500 transition-all"
                style={{ width: `${Math.max(4, percent)}%` }}
              />
            </div>
          )}
        </div>

        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setExpanded((v) => !v)}
          className="h-7 px-2 text-[11px] text-muted-foreground"
        >
          {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          <span className="ml-1">{expanded ? "Collapse" : "Details"}</span>
        </Button>

        {allDone && (
          <button
            type="button"
            onClick={onDismissAll}
            className="text-muted-foreground hover:text-destructive transition-colors mt-0.5"
            title="Dismiss"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {expanded && (
        <div className="border-t px-3 py-2 space-y-1 max-h-[280px] overflow-y-auto">
          {rows.length === 0 ? (
            <p className="text-[11px] text-muted-foreground py-1">Loading…</p>
          ) : (
            rows.map((j) => (
              <ParseJobRow key={j.job_id} job={j} />
            ))
          )}
        </div>
      )}
    </div>
  );
}


function ParseJobRow({ job }: { job: ParseJob }) {
  const icon =
    job.status === "done"
      ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
      : job.status === "error"
        ? <AlertTriangle className="h-3.5 w-3.5 text-destructive shrink-0" />
        : job.status === "cancelled"
          ? <X className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
          : <Loader2 className="h-3.5 w-3.5 text-muted-foreground animate-spin shrink-0" />;

  const tail =
    job.status === "running"
      ? (job.pages_total > 0 ? `${job.pages_done}/${job.pages_total} pages` : "starting…")
      : job.status === "done"
        ? `${job.pages_total || job.pages_done} pages · done`
        : job.status === "error"
          ? (job.error ?? "failed")
          : job.status === "cancelled"
            ? "cancelled"
            : "queued";

  return (
    <div className="flex items-center gap-2 text-[12px]">
      {icon}
      <FileText className="h-3 w-3 text-muted-foreground shrink-0" />
      <span className="truncate flex-1 min-w-0">{job.filename}</span>
      <span className={cn(
        "text-[10px] shrink-0",
        job.status === "error" ? "text-destructive" : "text-muted-foreground",
      )}>
        {tail}
      </span>
    </div>
  );
}
