"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Check,
  Coins,
  CreditCard,
  Download,
  Loader2,
  Receipt,
  Rocket,
  ShieldCheck,
  Sparkles,
  Wallet,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { BookCallButton } from "@/components/billing/book-call-button";
import { api } from "@/lib/api";
import { formatNumber, cn } from "@/lib/utils";
import {
  ENTERPRISE_INCLUDES,
  FREE_TRIAL_INCLUDES,
  planLabel as planLabelText,
  planToDisplay,
} from "@/lib/plans";
import type { BillingSummary, CreditTransactionEntry } from "@/lib/schema";

/**
 * Billing page — Phase 1 (read-only).
 *
 * Shows the user's plan tier, credit balances, trial-remaining counters,
 * the rate card, and recent credit_transactions. Top-up (Phase 2) and
 * Team subscription (Phase 3) plug in here.
 */

function planBadge(tier: BillingSummary["plan_tier"]): {
  label: string;
  tone: string;
} {
  // Two-tier UX — payg + team both surface as Enterprise. The amber
  // trial badge stays distinct so trial users know why they're capped.
  if (planToDisplay(tier) === "enterprise") {
    return {
      label: planLabelText(tier),
      tone: "bg-violet-500/15 text-violet-700 dark:text-violet-400",
    };
  }
  return {
    label: planLabelText(tier),
    tone: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
  };
}

function BalanceCard({
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
      <CardContent className="p-4 flex items-center gap-3">
        <div className={`h-10 w-10 rounded-md flex items-center justify-center shrink-0 ${toneClass}`}>
          <Icon className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium">{label}</p>
          <p className="text-2xl font-semibold leading-tight tabular-nums">{value}</p>
          {subtle && <p className="text-[11px] text-muted-foreground truncate">{subtle}</p>}
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * Plans — two tiles. One is the active plan (ring highlight + "Current"
 * badge); the other is the upgrade path (Book-a-call CTA). Enterprise
 * is a contact-sales flow, not a self-serve upgrade, so there is no
 * price — the co-founder works out the right package on the call.
 */
function PlansSection({
  tier,
  remaining,
}: {
  tier: BillingSummary["plan_tier"];
  remaining: BillingSummary["trial_remaining"];
}) {
  const display = planToDisplay(tier);
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      <PlanTile
        title="Free trial"
        subtitle="Start here — no card required"
        icon={Sparkles}
        tone="amber"
        current={display === "free_trial"}
        features={FREE_TRIAL_INCLUDES}
        footer={
          display === "free_trial" ? (
            <TrialRemainingGrid remaining={remaining} />
          ) : (
            <p className="text-[11px] text-muted-foreground">
              You&apos;ve moved past the free trial — Enterprise below.
            </p>
          )
        }
      />
      <PlanTile
        title="Enterprise"
        subtitle="Scaling up? Talk to the co-founder"
        icon={Rocket}
        tone="violet"
        current={display === "enterprise"}
        features={ENTERPRISE_INCLUDES}
        footer={
          display === "enterprise" ? (
            <p className="text-[11px] text-muted-foreground">
              Plan active. Need to adjust terms?{" "}
              <BookCallButton size="sm" variant="ghost" label="Book a call" className="h-6 px-2 text-[11px]" />
            </p>
          ) : (
            <div className="flex items-center gap-2">
              <BookCallButton size="sm" label="Book a call" />
              <span className="text-[11px] text-muted-foreground">
                ~15 min · no commitment
              </span>
            </div>
          )
        }
      />
    </div>
  );
}

function PlanTile({
  title,
  subtitle,
  icon: Icon,
  tone,
  current,
  features,
  footer,
}: {
  title: string;
  subtitle: string;
  icon: React.ComponentType<{ className?: string }>;
  tone: "amber" | "violet";
  current: boolean;
  features: readonly string[];
  footer: React.ReactNode;
}) {
  const toneBg =
    tone === "amber"
      ? "bg-amber-500/10 text-amber-600 dark:text-amber-400"
      : "bg-violet-500/10 text-violet-600 dark:text-violet-400";
  const ring = current
    ? tone === "amber"
      ? "ring-2 ring-amber-400/60"
      : "ring-2 ring-violet-400/60"
    : "";
  return (
    <Card className={cn("relative", ring, !current && "opacity-90")}>
      <CardContent className="p-5 space-y-4">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-start gap-3 min-w-0">
            <div className={cn("h-9 w-9 rounded-md flex items-center justify-center shrink-0", toneBg)}>
              <Icon className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h3 className="font-semibold leading-tight">{title}</h3>
                {current && (
                  <Badge variant="outline" className="text-[10px] py-0 h-4 px-1.5">
                    Current
                  </Badge>
                )}
              </div>
              <p className="text-[11px] text-muted-foreground mt-0.5">{subtitle}</p>
            </div>
          </div>
        </div>
        <ul className="space-y-1.5">
          {features.map((f) => (
            <li key={f} className="flex items-start gap-2 text-xs">
              <Check className="h-3.5 w-3.5 text-emerald-500 shrink-0 mt-0.5" />
              <span>{f}</span>
            </li>
          ))}
        </ul>
        <div className="pt-2 border-t">{footer}</div>
      </CardContent>
    </Card>
  );
}

function TrialRemainingGrid({
  remaining,
}: {
  remaining: BillingSummary["trial_remaining"];
}) {
  const items = [
    { label: "Workspace", n: remaining.workspaces },
    { label: "Build", n: remaining.builds },
    { label: "Chat turn", n: remaining.chats },
  ];
  return (
    <div className="grid grid-cols-3 gap-2 text-center">
      {items.map((it) => (
        <div key={it.label} className="rounded-md border border-border p-2">
          <div className="text-[9px] uppercase tracking-wide text-muted-foreground">{it.label}</div>
          <div className="text-base font-semibold tabular-nums">{it.n ?? "—"}</div>
          <div className="text-[9px] text-muted-foreground">remaining</div>
        </div>
      ))}
    </div>
  );
}

function RateCard() {
  const rows = [
    { meter: "LLM input tokens", rate: "0.30 credits / 1K", byok: "waived" },
    { meter: "LLM output tokens", rate: "0.60 credits / 1K", byok: "waived" },
    { meter: "Embedding tokens", rate: "0.01 credits / 1K", byok: "waived" },
    { meter: "Vector storage", rate: "10 credits / GB / month", byok: "charged" },
    { meter: "Graph payload storage", rate: "20 credits / GB / month", byok: "charged" },
    { meter: "Build baseline", rate: "5 credits / successful build", byok: "charged" },
    { meter: "Chat baseline", rate: "0.5 credits / chat turn", byok: "charged" },
  ];
  return (
    <Card>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-muted-foreground border-b border-border">
                <th className="text-left p-3 font-medium">Meter</th>
                <th className="text-left p-3 font-medium">Rate</th>
                <th className="text-left p-3 font-medium">With BYOK</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.meter} className="border-b border-border/50 last:border-b-0">
                  <td className="p-3">{r.meter}</td>
                  <td className="p-3 tabular-nums text-muted-foreground">{r.rate}</td>
                  <td className="p-3">
                    <Badge
                      variant="outline"
                      className={
                        r.byok === "waived"
                          ? "text-[10px] border-emerald-500/40 text-emerald-700 dark:text-emerald-400"
                          : "text-[10px] text-muted-foreground"
                      }
                    >
                      {r.byok}
                    </Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="px-3 py-2 border-t border-border text-[11px] text-muted-foreground">
          1 credit = $0.01 USD. Top-up bundles include bonus credits that never expire.
        </div>
      </CardContent>
    </Card>
  );
}

const REASON_LABEL: Record<string, string> = {
  build: "Build",
  build_failed: "Build (failed)",
  chat: "Chat",
  storage_daily: "Daily storage",
  topup: "Top-up",
  subscription_grant: "Subscription grant",
  refund: "Refund",
  adjustment: "Adjustment",
};

function TransactionsTable({ rows }: { rows: CreditTransactionEntry[] }) {
  if (rows.length === 0) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground text-center">
          No credit activity yet. Spend or top up to see entries here.
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardContent className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-muted-foreground border-b border-border">
                <th className="text-left p-3 font-medium">When</th>
                <th className="text-left p-3 font-medium">Reason</th>
                <th className="text-left p-3 font-medium">Bucket</th>
                <th className="text-right p-3 font-medium">Δ credits</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-border/50 last:border-b-0">
                  <td className="p-3 text-xs text-muted-foreground whitespace-nowrap">
                    {new Date(r.created_at).toLocaleString()}
                  </td>
                  <td className="p-3">
                    <div className="flex items-center gap-2">
                      <span>{REASON_LABEL[r.reason] ?? r.reason}</span>
                      {r.source_id && (
                        <span className="text-[10px] text-muted-foreground font-mono">
                          {r.source_id.slice(0, 10)}
                          {r.source_id.length > 10 ? "…" : ""}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="p-3 text-xs text-muted-foreground capitalize">{r.bucket}</td>
                  <td
                    className={
                      "p-3 text-right tabular-nums font-mono " +
                      (r.delta_credits > 0
                        ? "text-emerald-600 dark:text-emerald-400"
                        : r.delta_credits < 0
                        ? "text-rose-600 dark:text-rose-400"
                        : "text-muted-foreground")
                    }
                  >
                    {r.delta_credits > 0 ? "+" : ""}
                    {r.delta_credits}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}

export default function BillingPage() {
  const meQ = useQuery({ queryKey: ["me"], queryFn: api.me });
  const txQ = useQuery({
    queryKey: ["billing", "transactions"],
    queryFn: () => api.myBillingTransactions({ limit: 50 }),
  });

  if (meQ.isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground py-6">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading billing…
      </div>
    );
  }
  if (meQ.isError) {
    return <p className="text-sm text-destructive">{(meQ.error as Error).message}</p>;
  }
  const me = meQ.data!;
  const b = me.billing;
  const { label: planText, tone: planTone } = planBadge(b.plan_tier);

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center gap-3">
        <CreditCard className="h-5 w-5" />
        <h1 className="text-2xl font-semibold tracking-tight">Billing</h1>
        <Badge className={`text-[11px] ${planTone} border-transparent`}>{planText}</Badge>
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-medium">Plans</h2>
        <PlansSection tier={b.plan_tier} remaining={b.trial_remaining} />
      </section>

      <section className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <BalanceCard
          icon={Wallet}
          label="Total balance"
          value={formatNumber(b.total_credits)}
          subtle={`$${(b.total_credits / 100).toFixed(2)} USD value`}
          tone="sky"
        />
        <BalanceCard
          icon={ShieldCheck}
          label="Subscription credits"
          value={formatNumber(b.subscription_credits)}
          subtle="Reset monthly on renewal"
          tone="violet"
        />
        <BalanceCard
          icon={Coins}
          label="Top-up credits"
          value={formatNumber(b.topup_credits)}
          subtle="Never expire"
          tone="amber"
        />
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium">Rate card</h2>
        <RateCard />
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-sm font-medium flex items-center gap-2">
            <Receipt className="h-4 w-4" /> Recent activity
          </h2>
          <Button
            variant="outline"
            size="sm"
            onClick={async () => {
              // Authenticated download: fetch with Bearer, turn the body
              // into a Blob URL, click a hidden anchor. Avoids exposing
              // the token in a URL query param.
              try {
                const token =
                  typeof window !== "undefined"
                    ? window.localStorage.getItem("auth_token")
                    : null;
                if (!token) return;
                // `window.location.origin` falls back safely when
                // NEXT_PUBLIC_API_BASE is relative. Use the explicit base
                // so the download works in the split-domain deploy.
                const base = (process.env.NEXT_PUBLIC_API_BASE ?? "").replace(/\/$/, "");
                const url = `${base && !/^https?:\/\//i.test(base) ? `https://${base}` : base}/api/v1/me/billing/transactions.csv`;
                const res = await fetch(url, {
                  headers: { Authorization: `Bearer ${token}` },
                });
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const blob = await res.blob();
                const objectUrl = URL.createObjectURL(blob);
                const a = document.createElement("a");
                a.href = objectUrl;
                // Match the filename the server sends in Content-Disposition;
                // browsers will honour the response header when present.
                a.download = `billing-transactions.csv`;
                document.body.appendChild(a);
                a.click();
                a.remove();
                URL.revokeObjectURL(objectUrl);
              } catch (err) {
                console.error("CSV download failed:", err);
              }
            }}
            className="text-xs"
          >
            <Download className="h-3 w-3 mr-1" /> Download CSV
          </Button>
        </div>
        {txQ.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground py-3">
            <Loader2 className="h-4 w-4 animate-spin" /> Loading transactions…
          </div>
        ) : txQ.isError ? (
          <p className="text-sm text-destructive">{(txQ.error as Error).message}</p>
        ) : (
          <TransactionsTable rows={txQ.data?.entries ?? []} />
        )}
      </section>

      <p className="text-xs text-muted-foreground">
        Need help?{" "}
        <Link href="/profile" className="underline">
          Back to profile
        </Link>
      </p>
    </div>
  );
}
