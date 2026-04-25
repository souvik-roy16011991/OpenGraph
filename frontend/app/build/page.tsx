"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Hammer,
  ArrowRight,
  CheckCircle2,
  XCircle,
  Loader2,
  AlertTriangle,
  Play,
  Sparkles,
  FileText,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { WizardPage } from "@/components/wizard/wizard-page";
import { api } from "@/lib/api";
import type { BuildJobStatus } from "@/lib/schema";
import { useRequireWorkspace } from "@/hooks/use-require-workspace";
import { useBuildJob } from "@/hooks/use-build-job";
import { useWizardStore } from "@/store/wizard-store";

export default function BuildPage() {
  const router = useRouter();
  const params = useSearchParams();
  const activeWs = useRequireWorkspace();
  const markCompleted = useWizardStore((s) => s.markCompleted);
  const lastJobId = useWizardStore((s) => s.lastBuildJobId);
  const setLastBuildJobId = useWizardStore((s) => s.setLastBuildJobId);

  const [skipEmbeddings, setSkipEmbeddings] = React.useState(false);
  const [skipLlmCrossLinks, setSkipLlmCrossLinks] = React.useState(false);
  const [jobId, setJobId] = React.useState<string | null>(lastJobId);

  const currentQuery = useQuery({
    queryKey: ["current-build", activeWs],
    queryFn: () => api.currentBuild(),
    enabled: Boolean(activeWs),
    refetchOnMount: "always",
  });

  // Workspace files — used to gate /build behind "you have at least one file".
  // Backend rejects empty workspaces with a 500/error; catching it here gives
  // a clearer UX (CTA to /upload) than letting the build fail loudly.
  const filesQuery = useQuery({
    queryKey: ["workspace-files", activeWs],
    queryFn: () => api.listWorkspaceFiles(activeWs!),
    enabled: Boolean(activeWs),
  });
  const activeFileCount = filesQuery.data?.files.filter((f) => f.active).length ?? 0;

  // Most recent build for this workspace — if the user revisits /build without
  // anything running, we surface it instead of an empty page. useBuildJob then
  // takes over and pulls the full BuildJobStatus (with log_tail).
  const historyQuery = useQuery({
    queryKey: ["history-build-1", activeWs],
    queryFn: () => api.historyBuilds(1),
    enabled: Boolean(activeWs),
    staleTime: 30_000,
  });

  React.useEffect(() => {
    if (currentQuery.data?.running && currentQuery.data.job_id) {
      setJobId(currentQuery.data.job_id);
      setLastBuildJobId(currentQuery.data.job_id);
    }
  }, [currentQuery.data, setLastBuildJobId]);

  React.useEffect(() => {
    if (jobId) return;
    if (currentQuery.data?.running) return;
    const last = historyQuery.data?.builds?.[0];
    if (!last) return;
    setJobId(last.job_id);
  }, [jobId, currentQuery.data, historyQuery.data]);

  const job = useBuildJob(jobId);

  const startMutation = useMutation({
    mutationFn: () =>
      api.startBuild({
        skip_embeddings: skipEmbeddings,
        skip_llm_cross_links: skipLlmCrossLinks,
      }),
    onSuccess: (r) => {
      setJobId(r.job_id);
      setLastBuildJobId(r.job_id);
      toast.success("Build started", { description: `Job ${r.job_id.slice(0, 8)}…` });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  // ?autostart=1 — workspaces page links here when the user hits Re-run.
  // Fire once after we know nothing is already running.
  const autostart = params.get("autostart") === "1";
  const ranAutostartRef = React.useRef(false);
  React.useEffect(() => {
    if (ranAutostartRef.current) return;
    if (!activeWs) return;
    if (!autostart) return;
    if (currentQuery.isLoading) return;
    if (filesQuery.isLoading) return;
    if (activeFileCount === 0) return; // backend would reject; show pre-flight CTA instead
    ranAutostartRef.current = true;
    if (currentQuery.data?.running) return; // attach to the existing one
    startMutation.mutate();
    // startMutation.mutate is stable; eslint exhaustive-deps would loop us otherwise.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWs, autostart, currentQuery.isLoading, currentQuery.data, filesQuery.isLoading, activeFileCount]);

  const status = job.data?.status;
  React.useEffect(() => {
    if (status === "done") markCompleted("build", true);
  }, [status, markCompleted]);

  if (!activeWs) return null;

  const data = job.data;
  const isInFlight = data?.status === "queued" || data?.status === "running";
  const isDone = data?.status === "done";
  const hasNoFiles = !filesQuery.isLoading && activeFileCount === 0;

  if (hasNoFiles) {
    return (
      <WizardPage
        icon={Hammer}
        title="Run the build"
        description="Embeddings, edges, cross-knowledge base links. Watch it happen."
      >
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" /> Upload knowledge first
            </CardTitle>
            <CardDescription>
              You need at least one knowledge or tool JSON file before you can build.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button size="lg" onClick={() => router.push("/upload")}>
              Go to upload <ArrowRight className="h-4 w-4" />
            </Button>
          </CardContent>
        </Card>
      </WizardPage>
    );
  }

  return (
    <WizardPage
      icon={Hammer}
      title="Run the build"
      description="Embeddings, edges, cross-knowledge base links. Watch it happen."
    >
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5" /> Build options
          </CardTitle>
          <CardDescription>
            Skip the slow stages while iterating. Re-run with both off before going to production.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          <ToggleRow
            label="Skip embeddings"
            hint="Skip embedding generation and RELATED_TO edges. Much faster, but semantic search will be empty."
            value={skipEmbeddings}
            onChange={setSkipEmbeddings}
            disabled={isInFlight || startMutation.isPending}
          />
          <ToggleRow
            label="Skip LLM cross-knowledge base links"
            hint="Skip the LLM refinement pass for cross-KB IMPLEMENTS edges. Saves OpenRouter calls."
            value={skipLlmCrossLinks}
            onChange={setSkipLlmCrossLinks}
            disabled={isInFlight || startMutation.isPending}
          />

          <div className="flex items-center justify-between pt-2">
            <p className="text-xs text-muted-foreground">
              {isInFlight
                ? "A build is in progress for this workspace."
                : "Ready when you are. Builds typically take a few minutes."}
            </p>
            <Button
              size="lg"
              onClick={() => startMutation.mutate()}
              disabled={isInFlight || startMutation.isPending}
            >
              {startMutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" /> Starting…
                </>
              ) : isInFlight ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" /> Building…
                </>
              ) : (
                <>
                  <Play className="h-4 w-4" /> Start build
                </>
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      {data && <StatusCard data={data} />}

      {isDone && (
        <div className="flex items-center justify-end">
          <Button size="lg" onClick={() => router.push("/explore")}>
            Explore the graph <ArrowRight className="h-4 w-4" />
          </Button>
        </div>
      )}
    </WizardPage>
  );
}

function ToggleRow({
  label,
  hint,
  value,
  onChange,
  disabled,
}: {
  label: string;
  hint: string;
  value: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div className="min-w-0">
        <Label>{label}</Label>
        <p className="text-xs text-muted-foreground mt-1">{hint}</p>
      </div>
      <Switch checked={value} onCheckedChange={onChange} disabled={disabled} />
    </div>
  );
}

function StatusCard({ data }: { data: BuildJobStatus }) {
  const Icon = data.status === "done" ? CheckCircle2 : data.status === "error" ? XCircle : Loader2;
  const iconClass =
    data.status === "done"
      ? "text-emerald-500"
      : data.status === "error"
      ? "text-destructive"
      : "animate-spin text-sky-500";
  const badgeVariant: "success" | "destructive" | "secondary" =
    data.status === "done" ? "success" : data.status === "error" ? "destructive" : "secondary";

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Icon className={`h-5 w-5 ${iconClass}`} />
          {data.stage_name || "Build"}
          <Badge variant={badgeVariant} className="text-[10px]">
            {data.status}
          </Badge>
        </CardTitle>
        <CardDescription className="font-mono text-[11px]">job {data.job_id}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
            <span>
              Stage {data.stage} of 5 · {data.percent}%
            </span>
            <span className="tabular-nums font-mono">
              {formatElapsed(data.started_at, data.finished_at)}
            </span>
          </div>
          <Progress value={data.percent} />
        </div>

        {data.error && (
          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
            <div className="flex items-center gap-2 font-medium mb-1">
              <AlertTriangle className="h-3 w-3" /> Build failed
            </div>
            <pre className="whitespace-pre-wrap break-words font-mono text-[11px] max-h-48 overflow-auto">
              {data.error}
            </pre>
          </div>
        )}

        {data.log_tail.length > 0 && (
          <div>
            <Label className="text-xs text-muted-foreground">Live log (last 50 lines)</Label>
            <ScrollArea className="mt-1 rounded-md border bg-muted/30 h-56">
              <pre className="font-mono text-[11px] leading-snug p-3 whitespace-pre-wrap">
                {data.log_tail.join("\n")}
              </pre>
            </ScrollArea>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function formatElapsed(startedAt: number, finishedAt: number | null): string {
  if (!startedAt) return "";
  const end = finishedAt ?? Date.now() / 1000;
  const s = Math.max(0, Math.floor(end - startedAt));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}m ${r}s`;
}
