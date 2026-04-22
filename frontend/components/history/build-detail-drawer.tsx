"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Clock,
  Coins,
  Database,
  FileStack,
  Hash,
  Layers,
  Loader2,
  Sparkles,
  HardDrive,
} from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import {
  formatBytes,
  formatDuration,
  formatDurationMs,
  formatNumber,
} from "@/lib/utils";
import { parseBuildStats, type BuildHistoryDetail } from "@/lib/schema";

/** Small metric block — same visual language as the dashboard SummaryCard,
 *  but accepts a pre-formatted string value so we can show "1.6K" / "2.4 MB"
 *  without double-converting in the parent. */
function Metric({
  icon: Icon,
  label,
  value,
  subtle,
  tone = "sky",
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: string;
  subtle?: string;
  tone?: "sky" | "violet" | "amber" | "emerald" | "rose";
}) {
  const toneClass = {
    sky: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
    violet: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
    amber: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
    emerald: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    rose: "bg-rose-500/10 text-rose-600 dark:text-rose-400",
  }[tone];
  return (
    <Card>
      <CardContent className="p-3 flex items-center gap-3">
        <div
          className={`h-9 w-9 rounded-md flex items-center justify-center shrink-0 ${toneClass}`}
        >
          <Icon className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium">
            {label}
          </p>
          <p className="text-lg font-semibold leading-tight tabular-nums truncate">
            {value}
          </p>
          {subtle && (
            <p className="text-[11px] text-muted-foreground truncate">{subtle}</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function StageBar({
  label,
  ms,
  totalMs,
}: {
  label: string;
  ms: number;
  totalMs: number;
}) {
  const pct = totalMs > 0 ? Math.min(100, Math.round((ms / totalMs) * 100)) : 0;
  return (
    <div className="text-xs">
      <div className="flex justify-between text-muted-foreground mb-1">
        <span>{label}</span>
        <span className="font-mono tabular-nums">{formatDurationMs(ms)}</span>
      </div>
      <div className="h-1.5 rounded-full bg-muted overflow-hidden">
        <div
          className="h-full bg-sky-500 rounded-full transition-[width]"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

function DrawerBody({ row }: { row: BuildHistoryDetail }) {
  const stats = parseBuildStats(row.stats);
  const usage = stats.usage;
  const inputs = stats.inputs;
  const models = stats.models;
  const timings = stats.timings;

  const totalStageMs = timings?.stages_ms
    ? Object.values(timings.stages_ms).reduce((a, b) => a + (b ?? 0), 0)
    : 0;

  const hasUsage =
    usage &&
    (usage.llm_total_tokens > 0 ||
      usage.embedding_vectors > 0 ||
      usage.graph_payload_bytes > 0);

  return (
    <div className="space-y-4 max-h-[70vh] overflow-y-auto pr-1">
      {/* Status + id header */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="font-mono text-xs">{row.job_id}</span>
        <Badge
          variant={
            row.status === "done"
              ? "success"
              : row.status === "error"
              ? "destructive"
              : "secondary"
          }
          className="text-[10px]"
        >
          {row.status}
        </Badge>
        {row.backends?.graph && (
          <Badge variant="outline" className="text-[10px] font-mono">
            graph: {row.backends.graph}
          </Badge>
        )}
        {row.backends?.vectors && (
          <Badge variant="outline" className="text-[10px] font-mono">
            vec: {row.backends.vectors}
          </Badge>
        )}
      </div>

      {row.error && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="p-3">
            <p className="text-xs text-destructive font-mono whitespace-pre-wrap break-words">
              {row.error}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Usage grid */}
      {hasUsage && (
        <section>
          <h3 className="text-sm font-medium mb-2">Usage</h3>
          <div className="grid gap-2 sm:grid-cols-2">
            <Metric
              icon={Coins}
              label="LLM tokens"
              value={formatNumber(usage!.llm_total_tokens)}
              subtle={`${formatNumber(usage!.llm_prompt_tokens)} in · ${formatNumber(
                usage!.llm_completion_tokens,
              )} out · ${usage!.llm_calls} calls`}
              tone="violet"
            />
            <Metric
              icon={Sparkles}
              label="Embeddings"
              value={formatNumber(usage!.embedding_vectors)}
              subtle={
                usage!.embedding_dimension > 0
                  ? `dim ${usage!.embedding_dimension} · ${formatNumber(
                      usage!.embedding_prompt_tokens,
                    )} tok`
                  : undefined
              }
              tone="amber"
            />
            <Metric
              icon={Database}
              label="Graph payload"
              value={formatBytes(usage!.graph_payload_bytes)}
              subtle={`${formatNumber(stats.total_nodes ?? 0)} nodes · ${formatNumber(
                stats.total_edges ?? 0,
              )} edges`}
              tone="emerald"
            />
            <Metric
              icon={Clock}
              label="Duration"
              value={
                row.duration_s !== null
                  ? formatDuration(row.duration_s)
                  : timings?.total_ms
                  ? formatDurationMs(timings.total_ms)
                  : "—"
              }
              subtle={
                row.finished_at
                  ? `finished ${new Date(row.finished_at).toLocaleString()}`
                  : undefined
              }
              tone="sky"
            />
          </div>
        </section>
      )}

      {/* Stage timings */}
      {timings?.stages_ms && Object.keys(timings.stages_ms).length > 0 && (
        <section>
          <h3 className="text-sm font-medium mb-2">Stage timings</h3>
          <div className="space-y-2">
            {Object.entries(timings.stages_ms).map(([name, ms]) => (
              <StageBar key={name} label={name} ms={ms} totalMs={totalStageMs} />
            ))}
          </div>
        </section>
      )}

      {/* Inputs */}
      {inputs && (inputs.total_bytes > 0 || inputs.knowledge_files + inputs.tool_files > 0) && (
        <section>
          <h3 className="text-sm font-medium mb-2">Inputs</h3>
          <div className="grid gap-2 sm:grid-cols-2">
            <Metric
              icon={FileStack}
              label="Files"
              value={`${inputs.knowledge_files + inputs.tool_files}`}
              subtle={`${inputs.knowledge_files} knowledge · ${inputs.tool_files} tool`}
              tone="sky"
            />
            <Metric
              icon={HardDrive}
              label="Upload size"
              value={formatBytes(inputs.total_bytes)}
              tone="violet"
            />
          </div>
        </section>
      )}

      {/* Models */}
      {models && (models.llm || models.embedding) && (
        <section>
          <h3 className="text-sm font-medium mb-2">Models</h3>
          <dl className="grid gap-1 text-xs">
            {models.llm && (
              <div className="flex gap-2">
                <dt className="text-muted-foreground w-20 uppercase tracking-wide text-[10px] mt-[2px]">
                  LLM
                </dt>
                <dd className="font-mono break-all">{models.llm}</dd>
              </div>
            )}
            {models.embedding && (
              <div className="flex gap-2">
                <dt className="text-muted-foreground w-20 uppercase tracking-wide text-[10px] mt-[2px]">
                  Embedding
                </dt>
                <dd className="font-mono break-all">{models.embedding}</dd>
              </div>
            )}
          </dl>
        </section>
      )}

      {/* Node/edge type breakdown (already in stats, surface in drawer only) */}
      {(stats.nodes_by_type || stats.edges_by_type) && (
        <section>
          <h3 className="text-sm font-medium mb-2">Type breakdown</h3>
          <div className="grid gap-3 sm:grid-cols-2 text-xs">
            {stats.nodes_by_type && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                  Nodes
                </p>
                <ul className="space-y-0.5">
                  {Object.entries(stats.nodes_by_type).map(([t, n]) => (
                    <li key={t} className="flex justify-between font-mono tabular-nums">
                      <span className="text-muted-foreground">{t}</span>
                      <span>{n}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {stats.edges_by_type && (
              <div>
                <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
                  Edges
                </p>
                <ul className="space-y-0.5">
                  {Object.entries(stats.edges_by_type).map(([t, n]) => (
                    <li key={t} className="flex justify-between font-mono tabular-nums">
                      <span className="text-muted-foreground">{t}</span>
                      <span>{n}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Log tail */}
      {row.log_tail && row.log_tail.length > 0 && (
        <section>
          <h3 className="text-sm font-medium mb-2 flex items-center gap-2">
            <Hash className="h-3.5 w-3.5" /> Log tail
          </h3>
          <pre className="text-[11px] leading-snug font-mono bg-muted/40 rounded-md p-2 overflow-x-auto max-h-64">
            {row.log_tail.join("\n")}
          </pre>
        </section>
      )}
    </div>
  );
}

export function BuildDetailDrawer({
  jobId,
  open,
  onOpenChange,
}: {
  jobId: string | null;
  open: boolean;
  onOpenChange: (next: boolean) => void;
}) {
  const q = useQuery({
    queryKey: ["history", "build", jobId],
    queryFn: () => api.historyBuildDetail(jobId as string),
    enabled: open && !!jobId,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Layers className="h-4 w-4" /> Build detail
          </DialogTitle>
          <DialogDescription>
            Tokens, graph payload, stage timings and input bytes for this
            build.
          </DialogDescription>
        </DialogHeader>
        {q.isLoading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground py-6">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading build detail…
          </div>
        )}
        {q.isError && (
          <p className="text-sm text-destructive">
            {(q.error as Error).message}
          </p>
        )}
        {q.data && <DrawerBody row={q.data} />}
      </DialogContent>
    </Dialog>
  );
}
