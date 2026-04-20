"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  GitCompareArrows,
  ArrowLeftRight,
  ChevronDown,
  Briefcase,
  CheckCircle2,
  Loader2,
  AlertTriangle,
  Play,
  Clock,
  Database,
  Network,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type {
  GraphConfigPayload,
  GraphStats,
  QueryResponse,
  WorkspaceSummary,
} from "@/lib/schema";

/* -------------------------------------------------------------------------- */
/*  Workspace picker                                                          */
/* -------------------------------------------------------------------------- */

function WorkspacePicker({
  label,
  value,
  onChange,
  workspaces,
  disabledIds = [],
  accent,
}: {
  label: string;
  value: string | null;
  onChange: (id: string) => void;
  workspaces: WorkspaceSummary[];
  disabledIds?: string[];
  accent: "sky" | "emerald";
}) {
  const [open, setOpen] = React.useState(false);
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const selected = workspaces.find((w) => w.id === value) ?? null;
  const accentClass =
    accent === "sky"
      ? "border-sky-500/40 bg-sky-500/5"
      : "border-emerald-500/40 bg-emerald-500/5";
  const pillClass =
    accent === "sky"
      ? "bg-sky-500/15 text-sky-700 dark:text-sky-300"
      : "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";

  return (
    <div className={cn("rounded-lg border p-3", accentClass)} ref={ref}>
      <div className="flex items-center justify-between mb-2">
        <span
          className={cn(
            "text-[10px] uppercase tracking-wider font-semibold rounded px-2 py-0.5",
            pillClass,
          )}
        >
          {label}
        </span>
        {selected?.stats?.total_nodes !== undefined && (
          <span className="text-[10px] text-muted-foreground font-mono">
            {selected.stats.total_nodes}n · {selected.stats.total_edges ?? 0}e
          </span>
        )}
      </div>
      <div className="relative">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="w-full flex items-center gap-2 rounded-md border bg-background/80 px-3 py-2 text-left hover:border-foreground/40 transition-colors"
        >
          <Briefcase className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="flex-1 min-w-0">
            {selected ? (
              <>
                <span className="block text-sm font-medium truncate">
                  {selected.name}
                </span>
                <span className="block text-[10px] text-muted-foreground font-mono truncate">
                  k{selected.file_counts.knowledge}/t
                  {selected.file_counts.tool}
                  {selected.last_build_status
                    ? ` · ${selected.last_build_status}`
                    : ""}
                </span>
              </>
            ) : (
              <span className="block text-sm text-muted-foreground">
                Select workspace…
              </span>
            )}
          </span>
          <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
        </button>
        {open && (
          <div className="absolute top-full mt-1 left-0 right-0 rounded-md border bg-popover shadow-lg z-30 max-h-[320px] overflow-auto">
            {workspaces.length === 0 ? (
              <div className="px-3 py-3 text-xs text-muted-foreground">
                No workspaces. <Link href="/workspaces" className="underline">Create one</Link>.
              </div>
            ) : (
              workspaces.map((w) => {
                const disabled = disabledIds.includes(w.id);
                const selectedHere = w.id === value;
                return (
                  <button
                    key={w.id}
                    onClick={() => {
                      if (disabled) return;
                      onChange(w.id);
                      setOpen(false);
                    }}
                    disabled={disabled}
                    className={cn(
                      "w-full text-left px-3 py-2 text-sm hover:bg-accent transition-colors border-b last:border-b-0",
                      selectedHere && "bg-accent",
                      disabled && "opacity-40 cursor-not-allowed hover:bg-transparent",
                    )}
                  >
                    <span className="flex items-center gap-2">
                      {selectedHere && (
                        <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                      )}
                      <span className="truncate flex-1">{w.name}</span>
                      <span className="text-[10px] text-muted-foreground font-mono">
                        k{w.file_counts.knowledge}/t{w.file_counts.tool}
                      </span>
                    </span>
                  </button>
                );
              })
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Side-by-side data hooks                                                   */
/* -------------------------------------------------------------------------- */

function useWorkspaceBundle(ws_id: string | null) {
  const stats = useQuery({
    queryKey: ["pg-stats", ws_id],
    queryFn: () => api.statsFor(ws_id!),
    enabled: Boolean(ws_id),
    retry: false,
  });
  const cfg = useQuery({
    queryKey: ["pg-cfg", ws_id],
    queryFn: () => api.getGraphCfgFor(ws_id!),
    enabled: Boolean(ws_id),
    retry: false,
  });
  return { stats, cfg };
}

/* -------------------------------------------------------------------------- */
/*  Overview tab                                                              */
/* -------------------------------------------------------------------------- */

function OverviewColumn({
  ws,
  stats,
}: {
  ws: WorkspaceSummary | null;
  stats: GraphStats | undefined;
}) {
  if (!ws) return <EmptySide label="Pick a workspace to compare" />;
  return (
    <div className="space-y-3">
      <div>
        <p className="text-xs text-muted-foreground uppercase tracking-wider">
          Workspace
        </p>
        <p className="font-semibold">{ws.name}</p>
        {ws.description && (
          <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
            {ws.description}
          </p>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3 text-sm">
        <Metric label="Knowledge files" value={ws.file_counts.knowledge} />
        <Metric label="Tool files" value={ws.file_counts.tool} />
        <Metric
          label="Last build"
          value={ws.last_build_status ?? "—"}
          mono={false}
        />
        <Metric
          label="Built at"
          value={
            ws.last_build_at
              ? new Date(ws.last_build_at).toLocaleDateString()
              : "—"
          }
          mono={false}
        />
      </div>
      <div className="pt-2 border-t">
        <p className="text-xs text-muted-foreground uppercase tracking-wider mb-2 flex items-center gap-1.5">
          <Network className="h-3 w-3" /> Graph
        </p>
        {stats ? (
          <>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <Metric label="Total nodes" value={stats.total_nodes} />
              <Metric label="Total edges" value={stats.total_edges} />
            </div>
            <TypeBreakdown title="Nodes by type" map={stats.nodes_by_type} />
            <TypeBreakdown title="Edges by type" map={stats.edges_by_type} />
          </>
        ) : (
          <p className="text-xs text-muted-foreground italic">
            No graph yet — build this workspace first.
          </p>
        )}
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  mono = true,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div>
      <p className="text-[10px] uppercase text-muted-foreground tracking-wider">
        {label}
      </p>
      <p className={cn("text-sm font-medium", mono && "font-mono")}>{value}</p>
    </div>
  );
}

function TypeBreakdown({
  title,
  map,
}: {
  title: string;
  map: Record<string, number>;
}) {
  const entries = Object.entries(map ?? {}).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return null;
  return (
    <div className="mt-3">
      <p className="text-[10px] uppercase text-muted-foreground tracking-wider mb-1">
        {title}
      </p>
      <div className="flex flex-wrap gap-1">
        {entries.map(([k, v]) => (
          <span
            key={k}
            className="inline-flex items-center gap-1 rounded-md border bg-background/60 px-2 py-0.5 text-[11px] font-mono"
          >
            <span className="text-muted-foreground">{k}</span>
            <span>{v}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function EmptySide({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center min-h-[200px] text-sm text-muted-foreground italic">
      {label}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Configuration diff tab                                                    */
/* -------------------------------------------------------------------------- */

const CFG_SECTIONS: Array<{ key: keyof GraphConfigPayload; title: string }> = [
  { key: "embeddings", title: "Embeddings" },
  { key: "edges", title: "Edges" },
  { key: "cross_kb", title: "Cross-KB linking" },
  { key: "traversal", title: "Traversal" },
  { key: "search", title: "Search" },
  { key: "extraction", title: "Extraction" },
];

function formatVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) return v.length === 0 ? "[]" : JSON.stringify(v);
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function ConfigDiff({
  cfgA,
  cfgB,
}: {
  cfgA: GraphConfigPayload | undefined;
  cfgB: GraphConfigPayload | undefined;
}) {
  if (!cfgA && !cfgB)
    return (
      <EmptySide label="Pick two workspaces to see their configuration diff." />
    );

  return (
    <div className="space-y-5">
      {CFG_SECTIONS.map((section) => {
        const a = cfgA?.[section.key] as Record<string, unknown> | undefined;
        const b = cfgB?.[section.key] as Record<string, unknown> | undefined;
        const keys = Array.from(
          new Set([...Object.keys(a ?? {}), ...Object.keys(b ?? {})]),
        ).sort();
        const diffCount = keys.filter((k) => {
          const va = formatVal(a?.[k]);
          const vb = formatVal(b?.[k]);
          return va !== vb;
        }).length;

        return (
          <div key={section.key} className="rounded-lg border overflow-hidden">
            <div className="px-4 py-2.5 border-b bg-muted/40 flex items-center justify-between">
              <h3 className="text-sm font-semibold">{section.title}</h3>
              <span className="text-[11px] text-muted-foreground font-mono">
                {diffCount} diff{diffCount === 1 ? "" : "s"}
                {" · "}
                {keys.length} key{keys.length === 1 ? "" : "s"}
              </span>
            </div>
            <div className="divide-y">
              {keys.map((k) => {
                const va = formatVal(a?.[k]);
                const vb = formatVal(b?.[k]);
                const differs = va !== vb;
                return (
                  <div
                    key={k}
                    className={cn(
                      "grid grid-cols-[180px_1fr_1fr] gap-3 px-4 py-2 text-sm items-start",
                      differs && "bg-amber-500/5",
                    )}
                  >
                    <div className="flex items-center gap-1.5 min-w-0">
                      {differs && (
                        <span
                          aria-hidden
                          className="h-1.5 w-1.5 rounded-full bg-amber-500 shrink-0"
                        />
                      )}
                      <span
                        className={cn(
                          "font-mono text-xs truncate",
                          !differs && "text-muted-foreground",
                        )}
                        title={k}
                      >
                        {k}
                      </span>
                    </div>
                    <div
                      className={cn(
                        "font-mono text-xs break-all",
                        differs ? "text-sky-700 dark:text-sky-300 font-medium" : "text-muted-foreground",
                      )}
                    >
                      {va}
                    </div>
                    <div
                      className={cn(
                        "font-mono text-xs break-all",
                        differs ? "text-emerald-700 dark:text-emerald-300 font-medium" : "text-muted-foreground",
                      )}
                    >
                      {vb}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Query tab                                                                 */
/* -------------------------------------------------------------------------- */

interface SideResult {
  data?: QueryResponse;
  error?: string;
  loading: boolean;
}

function QueryCompare({
  wsA,
  wsB,
}: {
  wsA: WorkspaceSummary | null;
  wsB: WorkspaceSummary | null;
}) {
  const [prompt, setPrompt] = React.useState("");
  const [resA, setResA] = React.useState<SideResult>({ loading: false });
  const [resB, setResB] = React.useState<SideResult>({ loading: false });

  const mutation = useMutation({
    mutationFn: async () => {
      if (!prompt.trim() || !wsA?.id || !wsB?.id) return;
      setResA({ loading: true });
      setResB({ loading: true });
      const runOne = (ws_id: string) =>
        api
          .query({ query: prompt.trim() }, ws_id)
          .then((data) => ({ data, loading: false }) as SideResult)
          .catch((e: Error) => ({ error: e.message, loading: false }) as SideResult);

      const [a, b] = await Promise.all([runOne(wsA.id), runOne(wsB.id)]);
      setResA(a);
      setResB(b);
    },
  });

  const canRun = Boolean(wsA && wsB && prompt.trim() && !mutation.isPending);

  return (
    <div className="space-y-4">
      <div className="rounded-lg border bg-card p-4 space-y-3">
        <label className="block">
          <span className="text-xs uppercase tracking-wider text-muted-foreground font-medium mb-1.5 block">
            Ask both graphs the same question
          </span>
          <Textarea
            rows={3}
            placeholder="e.g. How is applicant income verified under the current policy?"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
          />
        </label>
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            Runs the query against both workspaces in parallel. Each uses its own graph config and LLM.
          </p>
          <Button onClick={() => mutation.mutate()} disabled={!canRun} size="lg">
            {mutation.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> Running on both…
              </>
            ) : (
              <>
                <Play className="h-4 w-4" /> Run on both
              </>
            )}
          </Button>
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <QueryResultCard label="A" workspace={wsA} result={resA} accent="sky" />
        <QueryResultCard label="B" workspace={wsB} result={resB} accent="emerald" />
      </div>
    </div>
  );
}

function QueryResultCard({
  label,
  workspace,
  result,
  accent,
}: {
  label: string;
  workspace: WorkspaceSummary | null;
  result: SideResult;
  accent: "sky" | "emerald";
}) {
  const pillClass =
    accent === "sky"
      ? "bg-sky-500/15 text-sky-700 dark:text-sky-300"
      : "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300";
  const borderClass =
    accent === "sky" ? "border-sky-500/30" : "border-emerald-500/30";
  const r = result.data;
  return (
    <Card className={cn("border", borderClass)}>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center justify-between gap-2 text-base">
          <span className="flex items-center gap-2">
            <span
              className={cn(
                "text-[10px] uppercase tracking-wider font-semibold rounded px-2 py-0.5",
                pillClass,
              )}
            >
              {label}
            </span>
            <span className="truncate">{workspace?.name ?? "—"}</span>
          </span>
          {r?.duration_ms !== undefined && r.duration_ms !== null && (
            <span className="text-[11px] text-muted-foreground font-mono flex items-center gap-1">
              <Clock className="h-3 w-3" /> {r.duration_ms}ms
            </span>
          )}
        </CardTitle>
        {r?.llm_model && (
          <CardDescription className="font-mono text-[11px]">
            model: {r.llm_model}
          </CardDescription>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {!workspace && (
          <p className="text-sm text-muted-foreground italic">
            Pick a workspace to compare against.
          </p>
        )}
        {workspace && result.loading && (
          <div className="flex items-center gap-2 text-sm text-muted-foreground py-4">
            <Loader2 className="h-4 w-4 animate-spin" /> Querying {workspace.name}…
          </div>
        )}
        {workspace && !result.loading && !r && !result.error && (
          <p className="text-sm text-muted-foreground italic">
            Enter a prompt and click Run on both.
          </p>
        )}
        {result.error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
            <pre className="whitespace-pre-wrap font-mono text-xs">
              {result.error}
            </pre>
          </div>
        )}
        {r && (
          <>
            <div>
              <p className="text-[10px] uppercase text-muted-foreground tracking-wider mb-1">
                Response
              </p>
              <div className="text-sm whitespace-pre-wrap leading-relaxed max-h-[400px] overflow-auto rounded-md border bg-background/50 p-3">
                {r.response}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <Badge variant="outline" className="justify-start">
                intent: {r.intent}
              </Badge>
              <Badge variant="outline" className="justify-start">
                focus: {r.kb_focus}
              </Badge>
            </div>
            {r.traversal_path && r.traversal_path.length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                  Traversal path · {r.traversal_path.length} steps
                </summary>
                <ol className="mt-2 space-y-0.5 list-decimal pl-4 font-mono text-[11px] text-muted-foreground max-h-[180px] overflow-auto">
                  {r.traversal_path.map((n, i) => (
                    <li key={i} className="truncate" title={n}>
                      {n}
                    </li>
                  ))}
                </ol>
              </details>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/*  Page                                                                      */
/* -------------------------------------------------------------------------- */

export default function PlaygroundPage() {
  const wsQuery = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.listWorkspaces,
    staleTime: 15_000,
  });
  const workspaces = wsQuery.data?.workspaces ?? [];

  const [aId, setAId] = React.useState<string | null>(null);
  const [bId, setBId] = React.useState<string | null>(null);

  // Auto-pick the two most recent workspaces on first load
  React.useEffect(() => {
    if (workspaces.length === 0) return;
    if (!aId && workspaces[0]) setAId(workspaces[0].id);
    if (!bId && workspaces[1]) setBId(workspaces[1].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaces.length]);

  const wsA = workspaces.find((w) => w.id === aId) ?? null;
  const wsB = workspaces.find((w) => w.id === bId) ?? null;

  const bundleA = useWorkspaceBundle(aId);
  const bundleB = useWorkspaceBundle(bId);

  const sameWs = Boolean(aId && aId === bId);

  const swap = () => {
    const tmp = aId;
    setAId(bId);
    setBId(tmp);
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
          <GitCompareArrows className="h-5 w-5" /> Playground
        </h1>
        <p className="text-muted-foreground text-sm">
          Compare two graph configurations side by side — their settings, their graph shape, and the answers they give to the same question.
        </p>
      </div>

      {wsQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading workspaces…</p>
      ) : workspaces.length < 2 ? (
        <Card className="border-amber-500/40 bg-amber-500/5">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <AlertTriangle className="h-4 w-4 text-amber-600" />
              Need at least two workspaces
            </CardTitle>
            <CardDescription>
              The Playground compares two workspaces with built graphs. You currently have {workspaces.length}.{" "}
              <Link href="/workspaces" className="underline font-medium">
                Create another workspace
              </Link>{" "}
              to start comparing.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-[1fr_auto_1fr] gap-3 items-center">
            <WorkspacePicker
              label="A"
              value={aId}
              onChange={setAId}
              workspaces={workspaces}
              disabledIds={bId ? [bId] : []}
              accent="sky"
            />
            <Button
              variant="outline"
              size="icon"
              onClick={swap}
              disabled={!aId || !bId}
              aria-label="Swap A and B"
              className="h-10 w-10 justify-self-center"
            >
              <ArrowLeftRight className="h-4 w-4" />
            </Button>
            <WorkspacePicker
              label="B"
              value={bId}
              onChange={setBId}
              workspaces={workspaces}
              disabledIds={aId ? [aId] : []}
              accent="emerald"
            />
          </div>

          {sameWs && (
            <div className="rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400 flex items-center gap-2">
              <AlertTriangle className="h-3.5 w-3.5" />
              Same workspace selected on both sides — pick two different workspaces to see a diff.
            </div>
          )}

          <Tabs defaultValue="overview">
            <TabsList>
              <TabsTrigger value="overview">
                <Database className="h-3.5 w-3.5 mr-1.5" /> Overview
              </TabsTrigger>
              <TabsTrigger value="config">Configuration</TabsTrigger>
              <TabsTrigger value="query">Query</TabsTrigger>
            </TabsList>

            <TabsContent value="overview">
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <Card className="border-sky-500/30">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base">Workspace A</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <OverviewColumn ws={wsA} stats={bundleA.stats.data} />
                  </CardContent>
                </Card>
                <Card className="border-emerald-500/30">
                  <CardHeader className="pb-3">
                    <CardTitle className="text-base">Workspace B</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <OverviewColumn ws={wsB} stats={bundleB.stats.data} />
                  </CardContent>
                </Card>
              </div>
            </TabsContent>

            <TabsContent value="config">
              {(bundleA.cfg.isLoading || bundleB.cfg.isLoading) && (
                <p className="text-sm text-muted-foreground flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" /> Loading configs…
                </p>
              )}
              {(bundleA.cfg.isError || bundleB.cfg.isError) && (
                <p className="text-sm text-destructive">
                  {(bundleA.cfg.error as Error)?.message ??
                    (bundleB.cfg.error as Error)?.message}
                </p>
              )}
              <div className="mb-3 grid grid-cols-[180px_1fr_1fr] gap-3 text-[10px] uppercase tracking-wider text-muted-foreground font-semibold">
                <span>Key</span>
                <span className="text-sky-700 dark:text-sky-300">A · {wsA?.name ?? "—"}</span>
                <span className="text-emerald-700 dark:text-emerald-300">B · {wsB?.name ?? "—"}</span>
              </div>
              <ConfigDiff cfgA={bundleA.cfg.data} cfgB={bundleB.cfg.data} />
            </TabsContent>

            <TabsContent value="query">
              <QueryCompare wsA={wsA} wsB={wsB} />
            </TabsContent>
          </Tabs>
        </>
      )}
    </div>
  );
}
