"use client";

import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  Loader2,
  X,
} from "lucide-react";

import { api } from "@/lib/api";
import type { ParseJob } from "@/lib/schema";
import { cn, formatBytes } from "@/lib/utils";

/**
 * Progress card for a single vision-OCR parse job.
 *
 * Polls `/api/v1/kb/parse-jobs/{id}` every 3 seconds while the job is
 * in flight; stops polling on terminal status. On success, invalidates
 * the workspace-files query so the "Files already in workspace" card
 * picks up the newly-attached JSON.
 *
 * Rendered only for raw-doc uploads (PDF/PPTX/DOCX/...). JSON uploads
 * land directly in workspace_files and don't go through this flow.
 */
export function ParseProgressCard({
  jobId,
  filename,
  sizeBytes,
  workspaceId,
  onDismiss,
}: {
  jobId: string;
  filename: string;
  sizeBytes: number;
  workspaceId: string | null;
  onDismiss: () => void;
}) {
  const qc = useQueryClient();

  const q = useQuery<ParseJob>({
    queryKey: ["parse-job", jobId],
    queryFn: () => api.getParseJob(jobId),
    // Poll every 3s until status is terminal. The function form reads
    // the current data and decides; returning false pauses the interval.
    refetchInterval: (query) => {
      const s = query.state.data?.status;
      if (s === "done" || s === "error" || s === "cancelled") return false;
      return 3000;
    },
    // Keep the card stable even if the network blinks.
    refetchIntervalInBackground: true,
    staleTime: 0,
  });

  const job = q.data;
  const status = job?.status ?? "queued";

  // Invalidate workspace-files exactly once when the job transitions to
  // done, so the upload page's existing file list re-fetches and the
  // new row shows up without a manual refresh.
  const seenDoneRef = React.useRef(false);
  React.useEffect(() => {
    if (status === "done" && !seenDoneRef.current && workspaceId) {
      seenDoneRef.current = true;
      qc.invalidateQueries({ queryKey: ["workspace-files", workspaceId] });
      qc.invalidateQueries({ queryKey: ["workspaces"] });
    }
  }, [status, workspaceId, qc]);

  const percent = job?.percent ?? 0;
  const pagesDone = job?.pages_done ?? 0;
  const pagesTotal = job?.pages_total ?? 0;

  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-md border px-3 py-2.5 bg-card text-sm",
        status === "error" && "border-destructive/50 bg-destructive/5",
        status === "done" && "border-emerald-500/40 bg-emerald-500/5",
      )}
    >
      <div className="mt-0.5 shrink-0">
        {status === "error" ? (
          <AlertTriangle className="h-4 w-4 text-destructive" />
        ) : status === "done" ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
        ) : (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        )}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 min-w-0">
          <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
          <p className="truncate font-medium">{filename}</p>
        </div>

        <p className="text-[11px] text-muted-foreground mt-0.5">
          {formatBytes(sizeBytes)}
          {status === "queued" && " · queued for parsing"}
          {status === "running" &&
            (pagesTotal > 0
              ? ` · page ${pagesDone}/${pagesTotal}`
              : " · starting up…")}
          {status === "done" &&
            ` · ${pagesTotal || pagesDone} page${(pagesTotal || pagesDone) === 1 ? "" : "s"} parsed`}
          {status === "cancelled" && " · cancelled"}
        </p>

        {/* Progress bar — visible until terminal. */}
        {(status === "queued" || status === "running") && (
          <div className="mt-2 h-1 w-full rounded bg-border/70 overflow-hidden">
            <div
              className="h-full bg-sky-500 transition-all"
              style={{ width: `${Math.max(4, Math.min(100, percent))}%` }}
            />
          </div>
        )}

        {status === "error" && (
          <p className="text-[11px] text-destructive mt-1 break-words">
            {job?.error ?? "Parse failed."}
          </p>
        )}
      </div>

      <button
        type="button"
        onClick={onDismiss}
        className="text-muted-foreground hover:text-destructive transition-colors mt-0.5"
        title="Dismiss"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
