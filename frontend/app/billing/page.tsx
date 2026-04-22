"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Coins,
  CreditCard,
  Loader2,
  Receipt,
  ShieldCheck,
  Sparkles,
  Wallet,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/lib/api";
import { formatNumber } from "@/lib/utils";
import type { BillingSummary, CreditTransactionEntry } from "@/lib/schema";

/**
 * Billing page — Phase 1 (read-only).
 *
 * Shows the user's plan tier, credit balances, trial-remaining counters,
 * the rate card, and recent credit_transactions. Top-up (Phase 2) and
 * Team subscription (Phase 3) plug in here.
 */

function planLabel(tier: BillingSummary["plan_tier"]): {
  label: string;
  tone: string;
} {
  switch (tier) {
    case "team":
      return { label: "Team", tone: "bg-violet-500/15 text-violet-700 dark:text-violet-400" };
    case "payg":
      return { label: "Pay-as-you-go", tone: "bg-sky-500/15 text-sky-700 dark:text-sky-400" };
    default:
      return { label: "Free trial", tone: "bg-amber-500/15 text-amber-700 dark:text-amber-400" };
  }
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

function TrialCta({ remaining }: { remaining: BillingSummary["trial_remaining"] }) {
  const items = [
    { label: "Workspace", n: remaining.workspaces },
    { label: "Build", n: remaining.builds },
    { label: "Chat turn", n: remaining.chats },
  ];
  return (
    <Card className="border-amber-400/50 bg-amber-500/5">
      <CardContent className="p-4 space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Sparkles className="h-4 w-4 text-amber-600 dark:text-amber-400" />
          You&apos;re on the free trial
        </div>
        <p className="text-xs text-muted-foreground">
          Limited to 1 workspace, 1 build, and 5 chat turns. Upgrade to pay-as-you-go by topping up credits.
        </p>
        <div className="grid grid-cols-3 gap-2 text-center">
          {items.map((it) => (
            <div key={it.label} className="rounded-md border border-border p-2">
              <div className="text-[10px] uppercase tracking-wide text-muted-foreground">{it.label}</div>
              <div className="text-lg font-semibold tabular-nums">{it.n ?? "—"}</div>
              <div className="text-[10px] text-muted-foreground">remaining</div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function TopUpBundles() {
  const bundles = [
    { name: "Starter", usd: 10, credits: 1000, bonus: 0 },
    { name: "Builder", usd: 25, credits: 2750, bonus: 10 },
    { name: "Pro", usd: 50, credits: 6000, bonus: 20 },
    { name: "Team", usd: 100, credits: 13000, bonus: 30 },
    { name: "Scale", usd: 250, credits: 35000, bonus: 40 },
  ];
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      {bundles.map((b) => (
        <Card key={b.name} className="border-border">
          <CardContent className="p-4 space-y-2">
            <div className="flex items-center justify-between">
              <div className="font-medium">{b.name}</div>
              {b.bonus > 0 && (
                <Badge variant="outline" className="text-[10px]">+{b.bonus}% bonus</Badge>
              )}
            </div>
            <div className="text-2xl font-semibold tabular-nums">${b.usd}</div>
            <div className="text-xs text-muted-foreground tabular-nums">
              {formatNumber(b.credits)} credits · ${(b.usd / b.credits).toFixed(4)}/credit
            </div>
            <Button size="sm" className="w-full" disabled title="Coming soon — Phase 2">
              Top up (coming soon)
            </Button>
          </CardContent>
        </Card>
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
  const { label: planText, tone: planTone } = planLabel(b.plan_tier);

  return (
    <div className="space-y-6 max-w-5xl">
      <div className="flex items-center gap-3">
        <CreditCard className="h-5 w-5" />
        <h1 className="text-2xl font-semibold tracking-tight">Billing</h1>
        <Badge className={`text-[11px] ${planTone} border-transparent`}>{planText}</Badge>
      </div>

      {b.plan_tier === "trial" ? (
        <TrialCta remaining={b.trial_remaining} />
      ) : null}

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
        <h2 className="text-sm font-medium">Top up</h2>
        <TopUpBundles />
        <p className="text-xs text-muted-foreground">
          Purchases are handled by Stripe (coming in Phase 2). Paid bundles flip your account from the free trial to pay-as-you-go automatically.
        </p>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium">Rate card</h2>
        <RateCard />
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-medium flex items-center gap-2">
          <Receipt className="h-4 w-4" /> Recent activity
        </h2>
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
