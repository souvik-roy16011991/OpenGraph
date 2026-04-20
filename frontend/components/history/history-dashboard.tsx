"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Hammer,
  MessageSquareText,
  UploadCloud,
  Settings2,
  TrendingUp,
  CheckCircle2,
  XCircle,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatDuration } from "@/lib/utils";
import type {
  BuildHistoryRow,
  ChatHistoryRow,
  ConfigHistoryRow,
  UploadHistoryRow,
} from "@/lib/schema";

/* -------------------------------------------------------------------------- */
/*  Aggregation                                                               */
/* -------------------------------------------------------------------------- */

const DAYS = 30;

type DayBucket = {
  date: string; // YYYY-MM-DD
  builds: number;
  chats: number;
  uploads: number;
  configs: number;
};

function buildBuckets(
  builds: BuildHistoryRow[],
  chats: ChatHistoryRow[],
  uploads: UploadHistoryRow[],
  configs: ConfigHistoryRow[],
): DayBucket[] {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const buckets: DayBucket[] = [];
  const index: Record<string, DayBucket> = {};
  for (let i = DAYS - 1; i >= 0; i--) {
    const d = new Date(today);
    d.setDate(today.getDate() - i);
    const key = d.toISOString().slice(0, 10);
    const b: DayBucket = { date: key, builds: 0, chats: 0, uploads: 0, configs: 0 };
    buckets.push(b);
    index[key] = b;
  }
  const bump = (iso: string | null | undefined, field: keyof DayBucket) => {
    if (!iso) return;
    const key = iso.slice(0, 10);
    const b = index[key];
    if (b && field !== "date") (b[field] as number) += 1;
  };
  for (const r of builds) bump(r.created_at, "builds");
  for (const r of chats) bump(r.last_activity_at, "chats");
  for (const r of uploads) bump(r.created_at, "uploads");
  for (const r of configs) bump(r.created_at, "configs");
  return buckets;
}

/* -------------------------------------------------------------------------- */
/*  Summary cards                                                             */
/* -------------------------------------------------------------------------- */

function SummaryCard({
  icon: Icon,
  label,
  value,
  subtle,
  tone,
}: {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  value: number;
  subtle?: string;
  tone: "sky" | "violet" | "amber" | "emerald";
}) {
  const toneClass = {
    sky: "bg-sky-500/10 text-sky-600 dark:text-sky-400",
    violet: "bg-violet-500/10 text-violet-600 dark:text-violet-400",
    amber: "bg-amber-500/10 text-amber-600 dark:text-amber-400",
    emerald: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  }[tone];
  return (
    <Card>
      <CardContent className="p-4 flex items-center gap-3">
        <div className={cn("h-10 w-10 rounded-md flex items-center justify-center shrink-0", toneClass)}>
          <Icon className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium">
            {label}
          </p>
          <p className="text-2xl font-semibold leading-tight tabular-nums">{value}</p>
          {subtle && <p className="text-[11px] text-muted-foreground truncate">{subtle}</p>}
        </div>
      </CardContent>
    </Card>
  );
}

/* -------------------------------------------------------------------------- */
/*  Activity chart (stacked bars)                                             */
/* -------------------------------------------------------------------------- */

const SERIES: Array<{ key: keyof DayBucket; label: string; color: string }> = [
  { key: "builds", label: "Builds", color: "hsl(200 95% 55%)" },     // sky
  { key: "chats", label: "Chats", color: "hsl(262 80% 60%)" },        // violet
  { key: "uploads", label: "Uploads", color: "hsl(38 92% 55%)" },     // amber
  { key: "configs", label: "Configs", color: "hsl(158 65% 45%)" },    // emerald
];

function ActivityChart({ buckets }: { buckets: DayBucket[] }) {
  const [hoverIdx, setHoverIdx] = React.useState<number | null>(null);
  const max = Math.max(
    1,
    ...buckets.map((b) => b.builds + b.chats + b.uploads + b.configs),
  );

  const W = 720;
  const H = 160;
  const padL = 28;
  const padR = 8;
  const padT = 8;
  const padB = 24;
  const chartW = W - padL - padR;
  const chartH = H - padT - padB;
  const barW = chartW / buckets.length;
  const innerBarW = Math.max(2, barW * 0.7);

  const gridTicks = [0, 0.5, 1].map((t) => Math.round(max * t));

  return (
    <div className="w-full overflow-hidden">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-[180px]"
        preserveAspectRatio="none"
        onMouseLeave={() => setHoverIdx(null)}
      >
        {/* gridlines */}
        {gridTicks.map((t, i) => {
          const y = padT + chartH - (t / max) * chartH;
          return (
            <g key={i}>
              <line
                x1={padL}
                x2={W - padR}
                y1={y}
                y2={y}
                stroke="currentColor"
                strokeOpacity={0.08}
              />
              <text
                x={padL - 6}
                y={y + 3}
                textAnchor="end"
                className="fill-muted-foreground"
                fontSize={9}
              >
                {t}
              </text>
            </g>
          );
        })}

        {/* bars */}
        {buckets.map((b, i) => {
          const x = padL + i * barW + (barW - innerBarW) / 2;
          let stackY = padT + chartH;
          const total = b.builds + b.chats + b.uploads + b.configs;
          return (
            <g key={b.date}>
              <rect
                x={padL + i * barW}
                y={padT}
                width={barW}
                height={chartH}
                fill="transparent"
                onMouseEnter={() => setHoverIdx(i)}
              />
              {SERIES.map((s) => {
                const v = b[s.key] as number;
                if (!v) return null;
                const h = (v / max) * chartH;
                stackY -= h;
                return (
                  <rect
                    key={s.key}
                    x={x}
                    y={stackY}
                    width={innerBarW}
                    height={h}
                    fill={s.color}
                    rx={1.5}
                    opacity={hoverIdx === null || hoverIdx === i ? 1 : 0.45}
                  />
                );
              })}
              {hoverIdx === i && total > 0 && (
                <text
                  x={x + innerBarW / 2}
                  y={padT + chartH - (total / max) * chartH - 4}
                  textAnchor="middle"
                  fontSize={10}
                  className="fill-foreground font-medium"
                >
                  {total}
                </text>
              )}
            </g>
          );
        })}

        {/* x-axis labels: every ~7 days */}
        {buckets.map((b, i) => {
          if (i % 7 !== 0 && i !== buckets.length - 1) return null;
          const x = padL + i * barW + barW / 2;
          const d = new Date(b.date);
          const label = `${d.getMonth() + 1}/${d.getDate()}`;
          return (
            <text
              key={b.date}
              x={x}
              y={H - 8}
              textAnchor="middle"
              fontSize={9}
              className="fill-muted-foreground"
            >
              {label}
            </text>
          );
        })}
      </svg>

      <div className="flex flex-wrap gap-3 text-[11px] mt-2 px-1">
        {SERIES.map((s) => {
          const total = buckets.reduce((sum, b) => sum + (b[s.key] as number), 0);
          return (
            <span key={s.key} className="inline-flex items-center gap-1.5">
              <span
                aria-hidden
                className="h-2.5 w-2.5 rounded-sm"
                style={{ background: s.color }}
              />
              <span className="text-muted-foreground">{s.label}</span>
              <span className="font-mono tabular-nums">{total}</span>
            </span>
          );
        })}
        {hoverIdx !== null && (
          <span className="ml-auto font-mono text-muted-foreground">
            {new Date(buckets[hoverIdx].date).toLocaleDateString(undefined, {
              month: "short",
              day: "numeric",
              year: "numeric",
            })}
          </span>
        )}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Build success donut                                                       */
/* -------------------------------------------------------------------------- */

function SuccessDonut({ builds }: { builds: BuildHistoryRow[] }) {
  const done = builds.filter((b) => b.status === "done").length;
  const err = builds.filter((b) => b.status === "error").length;
  const inflight = builds.filter(
    (b) => b.status === "running" || b.status === "queued",
  ).length;
  const total = done + err + inflight;
  const rate = total > 0 ? Math.round((done / total) * 100) : 0;

  // SVG donut math
  const R = 42;
  const C = 2 * Math.PI * R;
  const doneFrac = total ? done / total : 0;
  const errFrac = total ? err / total : 0;

  return (
    <div className="flex items-center gap-5">
      <div className="relative shrink-0">
        <svg width="110" height="110" viewBox="0 0 110 110">
          <circle
            cx="55"
            cy="55"
            r={R}
            fill="none"
            stroke="currentColor"
            strokeOpacity={0.08}
            strokeWidth={12}
          />
          {total > 0 && (
            <>
              <circle
                cx="55"
                cy="55"
                r={R}
                fill="none"
                stroke="hsl(158 65% 45%)"
                strokeWidth={12}
                strokeDasharray={`${doneFrac * C} ${C}`}
                strokeDashoffset={C * 0.25}
                transform="rotate(-90 55 55)"
                strokeLinecap="butt"
              />
              <circle
                cx="55"
                cy="55"
                r={R}
                fill="none"
                stroke="hsl(0 72% 55%)"
                strokeWidth={12}
                strokeDasharray={`${errFrac * C} ${C}`}
                strokeDashoffset={C * 0.25 - doneFrac * C}
                transform="rotate(-90 55 55)"
              />
            </>
          )}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="text-2xl font-semibold tabular-nums leading-none">
            {rate}%
          </span>
          <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
            success
          </span>
        </div>
      </div>
      <div className="space-y-1.5 text-xs">
        <LegendRow color="hsl(158 65% 45%)" label="Completed" value={done} icon={CheckCircle2} />
        <LegendRow color="hsl(0 72% 55%)" label="Failed" value={err} icon={XCircle} />
        {inflight > 0 && (
          <LegendRow
            color="hsl(200 95% 55%)"
            label="In flight"
            value={inflight}
          />
        )}
      </div>
    </div>
  );
}

function LegendRow({
  color,
  label,
  value,
  icon: Icon,
}: {
  color: string;
  label: string;
  value: number;
  icon?: React.ComponentType<{ className?: string }>;
}) {
  return (
    <div className="flex items-center gap-2">
      <span
        aria-hidden
        className="h-2.5 w-2.5 rounded-sm shrink-0"
        style={{ background: color }}
      />
      {Icon && <Icon className="h-3 w-3 text-muted-foreground" />}
      <span className="text-muted-foreground">{label}</span>
      <span className="ml-auto font-mono tabular-nums">{value}</span>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Build duration sparkline                                                  */
/* -------------------------------------------------------------------------- */

function DurationSparkline({ builds }: { builds: BuildHistoryRow[] }) {
  const series = builds
    .filter((b) => b.status === "done" && typeof b.duration_s === "number")
    .slice(0, 20)
    .reverse(); // oldest → newest left-to-right

  if (series.length === 0) {
    return (
      <div className="flex items-center justify-center h-[110px] text-xs text-muted-foreground italic">
        No completed builds yet.
      </div>
    );
  }

  const max = Math.max(...series.map((b) => b.duration_s ?? 0));
  const avg =
    series.reduce((s, b) => s + (b.duration_s ?? 0), 0) / series.length;

  const W = 260;
  const H = 80;
  const pad = 6;
  const innerW = W - pad * 2;
  const innerH = H - pad * 2;

  const points = series.map((b, i) => {
    const x = pad + (i * innerW) / Math.max(1, series.length - 1);
    const y = pad + innerH - ((b.duration_s ?? 0) / max) * innerH;
    return { x, y, v: b.duration_s ?? 0 };
  });

  const path = points.map((p, i) => `${i === 0 ? "M" : "L"}${p.x},${p.y}`).join(" ");
  const areaPath = `${path} L ${points[points.length - 1].x},${pad + innerH} L ${points[0].x},${pad + innerH} Z`;
  const avgY = pad + innerH - (avg / max) * innerH;

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground flex items-center gap-1">
          <TrendingUp className="h-3 w-3" /> last {series.length} builds
        </span>
        <span className="font-mono tabular-nums">avg {formatDuration(avg)}</span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-[100px]" preserveAspectRatio="none">
        <defs>
          <linearGradient id="spark-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="hsl(200 95% 55%)" stopOpacity={0.35} />
            <stop offset="100%" stopColor="hsl(200 95% 55%)" stopOpacity={0} />
          </linearGradient>
        </defs>
        <line
          x1={pad}
          x2={W - pad}
          y1={avgY}
          y2={avgY}
          stroke="currentColor"
          strokeOpacity={0.2}
          strokeDasharray="3 3"
        />
        <path d={areaPath} fill="url(#spark-fill)" />
        <path d={path} fill="none" stroke="hsl(200 95% 55%)" strokeWidth={1.75} />
        {points.map((p, i) => (
          <circle key={i} cx={p.x} cy={p.y} r={2.5} fill="hsl(200 95% 55%)" />
        ))}
      </svg>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Dashboard                                                                 */
/* -------------------------------------------------------------------------- */

export function HistoryDashboard() {
  const buildsQ = useQuery({
    queryKey: ["history", "builds"],
    queryFn: () => api.historyBuilds(100),
    refetchInterval: 15_000,
  });
  const chatsQ = useQuery({
    queryKey: ["history", "chats"],
    queryFn: () => api.historyChats(100),
    refetchInterval: 30_000,
  });
  const uploadsQ = useQuery({
    queryKey: ["history", "uploads"],
    queryFn: () => api.historyUploads(undefined, 100),
    refetchInterval: 30_000,
  });
  const configsQ = useQuery({
    queryKey: ["history", "configs"],
    queryFn: () => api.historyConfigs(undefined, 100),
    refetchInterval: 30_000,
  });

  const builds = buildsQ.data?.builds ?? [];
  const chats = chatsQ.data?.chats ?? [];
  const uploads = uploadsQ.data?.uploads ?? [];
  const configs = configsQ.data?.configs ?? [];

  const buckets = React.useMemo(
    () => buildBuckets(builds, chats, uploads, configs),
    [builds, chats, uploads, configs],
  );

  const last7Total = buckets.slice(-7).reduce(
    (s, b) => s + b.builds + b.chats + b.uploads + b.configs,
    0,
  );

  const mostRecent = (iso?: string | null) =>
    iso ? new Date(iso).toLocaleDateString() : "—";

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <SummaryCard
          icon={Hammer}
          label="Builds"
          value={builds.length}
          subtle={`last: ${mostRecent(builds[0]?.created_at)}`}
          tone="sky"
        />
        <SummaryCard
          icon={MessageSquareText}
          label="Chat sessions"
          value={chats.length}
          subtle={`last: ${mostRecent(chats[0]?.last_activity_at)}`}
          tone="violet"
        />
        <SummaryCard
          icon={UploadCloud}
          label="Uploads"
          value={uploads.length}
          subtle={`last: ${mostRecent(uploads[0]?.created_at)}`}
          tone="amber"
        />
        <SummaryCard
          icon={Settings2}
          label="Config changes"
          value={configs.length}
          subtle={`last: ${mostRecent(configs[0]?.created_at)}`}
          tone="emerald"
        />
      </div>

      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <CardTitle className="text-base">Activity · last 30 days</CardTitle>
            <span className="text-xs text-muted-foreground tabular-nums">
              {last7Total} event{last7Total === 1 ? "" : "s"} this week
            </span>
          </div>
        </CardHeader>
        <CardContent>
          <ActivityChart buckets={buckets} />
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Build outcomes</CardTitle>
          </CardHeader>
          <CardContent>
            <SuccessDonut builds={builds} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Build duration</CardTitle>
          </CardHeader>
          <CardContent>
            <DurationSparkline builds={builds} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
