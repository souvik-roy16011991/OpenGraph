"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import {
  Upload,
  Tag,
  Sliders,
  Hammer,
  Network,
  MessageSquareText,
  CheckCircle2,
  Lock,
  Moon,
  Sun,
  Activity,
  History as HistoryIcon,
  Briefcase,
  ChevronDown,
} from "lucide-react";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { UserMenu } from "@/components/user-menu";
import { useGraphStatus } from "@/hooks/use-graph-status";
import { useWizardStore, type WizardStep } from "@/store/wizard-store";
import { useWorkspaceStore } from "@/store/workspace-store";

interface NavItem {
  step: WizardStep;
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  desc: string;
  requires?: WizardStep[];
}

const NAV: NavItem[] = [
  { step: "upload",       href: "/upload",       label: "Upload KB",     icon: Upload,            desc: "Knowledge + tool JSONs" },
  { step: "domain",       href: "/domain",       label: "Domain",        icon: Tag,               desc: "Fill domain.yaml fields", requires: ["upload"] },
  { step: "graph-config", href: "/graph-config", label: "Graph Config",  icon: Sliders,           desc: "Tune graph.yaml knobs",   requires: ["upload", "domain"] },
  { step: "build",        href: "/build",        label: "Build",         icon: Hammer,            desc: "Run the build pipeline",  requires: ["upload", "domain"] },
  { step: "explore",      href: "/explore",      label: "Explore",       icon: Network,           desc: "Interactive graph viz",   requires: ["build"] },
  { step: "query",        href: "/query",        label: "Query",         icon: MessageSquareText, desc: "Chat with the agent",     requires: ["build"] },
];

const SECONDARY_NAV = [
  { href: "/history", label: "History", desc: "Audit trail",   icon: HistoryIcon },
];

function WorkspaceSwitcher() {
  const activeId = useWorkspaceStore((s) => s.activeId);
  const setActiveId = useWorkspaceStore((s) => s.setActiveId);
  const { data } = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.listWorkspaces,
    refetchInterval: 15_000,
  });
  const list = data?.workspaces ?? [];
  const active = list.find((w) => w.id === activeId) ?? null;

  return (
    <div className="relative group">
      <Link
        href="/workspaces"
        className="w-full flex items-center gap-2 rounded-md border bg-background/60 px-3 py-2 text-left hover:border-foreground/40 transition-colors"
      >
        <Briefcase className="h-4 w-4 shrink-0 text-muted-foreground" />
        <span className="flex-1 min-w-0">
          {active ? (
            <>
              <span className="block text-sm font-medium truncate">{active.name}</span>
              <span className="block text-[10px] text-muted-foreground font-mono truncate">
                k{active.file_counts.knowledge}/t{active.file_counts.tool}
                {active.stats?.total_nodes !== undefined ? ` · ${active.stats.total_nodes}n` : ""}
              </span>
            </>
          ) : (
            <span className="block text-sm text-muted-foreground">Select workspace…</span>
          )}
        </span>
        <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
      </Link>
      {list.length > 0 && (
        <div className="absolute left-0 right-0 top-full mt-1 rounded-md border bg-popover shadow-lg z-20 hidden group-hover:block max-h-[300px] overflow-auto">
          {list.map((w) => (
            <button
              key={w.id}
              onClick={() => setActiveId(w.id)}
              className={cn(
                "w-full text-left px-3 py-2 text-sm hover:bg-accent transition-colors border-b last:border-b-0",
                w.id === activeId && "bg-accent"
              )}
            >
              <span className="flex items-center gap-2">
                {w.id === activeId && <CheckCircle2 className="h-3 w-3 text-emerald-500" />}
                <span className="truncate flex-1">{w.name}</span>
                <span className="text-[10px] text-muted-foreground font-mono">
                  k{w.file_counts.knowledge}/t{w.file_counts.tool}
                </span>
              </span>
            </button>
          ))}
          <Link
            href="/workspaces"
            className="block w-full text-left px-3 py-2 text-xs text-muted-foreground hover:bg-accent hover:text-foreground border-t"
          >
            Manage workspaces →
          </Link>
        </div>
      )}
    </div>
  );
}

function StatusPill() {
  const status = useGraphStatus();
  if (status.state === "no_workspace")
    return <Badge variant="outline">Select a workspace</Badge>;
  if (status.state === "loading")
    return <Badge variant="outline" className="gap-1.5"><Activity className="h-3 w-3 animate-pulse" /> Checking…</Badge>;
  if (status.state === "ready")
    return (
      <Badge variant="success" className="gap-1.5">
        <CheckCircle2 className="h-3 w-3" /> Graph ready · {status.nodes} nodes · {status.edges} edges
      </Badge>
    );
  if (status.state === "empty") return <Badge variant="warning">No graph built yet</Badge>;
  return <Badge variant="destructive">Error: {status.message}</Badge>;
}

function BackendBadges() {
  const activeWs = useWorkspaceStore((s) => s.activeId);
  const { data } = useQuery({
    queryKey: ["stats-backends", activeWs],
    queryFn: api.stats,
    enabled: Boolean(activeWs),
    refetchInterval: 15_000,
  });
  const b = data?.backends;
  if (!b) return null;
  const style = (val?: string) => {
    if (!val) return "outline" as const;
    if (val === "memgraph" || val === "qdrant" || val === "neo4j" || val === "pinecone") return "secondary" as const;
    return "outline" as const;
  };
  return (
    <div className="hidden md:flex items-center gap-1">
      <Badge variant={style(b.graph)} className="text-[10px] font-mono">graph: {b.graph}</Badge>
      <Badge variant={style(b.vectors)} className="text-[10px] font-mono">vec: {b.vectors}</Badge>
    </div>
  );
}

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  if (!mounted) return <div className="h-9 w-9" />;
  return (
    <Button variant="ghost" size="icon" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Toggle theme">
      {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </Button>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const completed = useWizardStore((s) => s.completed);
  const activeWs = useWorkspaceStore((s) => s.activeId);

  function isLocked(item: NavItem) {
    if (!activeWs && item.step !== "upload") return true;
    if (!activeWs) return true;
    return (item.requires || []).some((r) => !completed[r]);
  }

  return (
    <div className="grid grid-cols-[260px_1fr] min-h-screen">
      {/* Sidebar */}
      <aside className="border-r bg-card/50 flex flex-col">
        <div className="h-14 border-b flex items-center gap-2 px-5">
          <div className="h-7 w-7 rounded bg-gradient-to-br from-indigo-500 to-emerald-500 shadow-sm" />
          <div className="flex flex-col">
            <span className="text-sm font-semibold leading-tight">KB Engine</span>
            <span className="text-[10px] text-muted-foreground uppercase tracking-wider">Knowledge graph</span>
          </div>
        </div>
        <div className="p-3 border-b">
          <WorkspaceSwitcher />
        </div>
        <nav className="p-3 flex flex-col gap-1">
          {NAV.map((item, idx) => {
            const active = pathname?.startsWith(item.href);
            const locked = isLocked(item);
            const done = completed[item.step];
            return (
              <Link
                key={item.href}
                href={locked ? "#" : item.href}
                onClick={(e) => locked && e.preventDefault()}
                className={cn(
                  "group flex items-start gap-3 rounded-md px-3 py-2.5 text-sm transition-colors",
                  active && "bg-accent text-accent-foreground",
                  !active && !locked && "hover:bg-accent/60",
                  locked && "opacity-50 cursor-not-allowed"
                )}
                aria-disabled={locked}
              >
                <span className="relative mt-0.5">
                  <item.icon className="h-4 w-4" />
                  {done && (
                    <CheckCircle2 className="absolute -right-1.5 -top-1.5 h-3 w-3 text-emerald-500" />
                  )}
                </span>
                <span className="flex flex-col flex-1 min-w-0">
                  <span className="flex items-center gap-1.5">
                    <span className="text-[10px] font-mono text-muted-foreground">{String(idx + 1).padStart(2, "0")}</span>
                    <span className="font-medium">{item.label}</span>
                    {locked && <Lock className="h-3 w-3 text-muted-foreground ml-auto" />}
                  </span>
                  <span className="text-[11px] text-muted-foreground truncate">{item.desc}</span>
                </span>
              </Link>
            );
          })}

          <div className="h-px bg-border my-3 mx-2" />
          {SECONDARY_NAV.map((item) => {
            const active = pathname?.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "group flex items-start gap-3 rounded-md px-3 py-2.5 text-sm transition-colors",
                  active && "bg-accent text-accent-foreground",
                  !active && "hover:bg-accent/60"
                )}
              >
                <item.icon className="h-4 w-4 mt-0.5" />
                <span className="flex flex-col flex-1 min-w-0">
                  <span className="font-medium">{item.label}</span>
                  <span className="text-[11px] text-muted-foreground truncate">{item.desc}</span>
                </span>
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto p-4 border-t">
          <p className="text-[10px] text-muted-foreground">
            Single-domain edition · v1.0
          </p>
        </div>
      </aside>

      {/* Main */}
      <div className="flex flex-col min-w-0">
        <header className="h-14 border-b px-6 flex items-center justify-between gap-4 bg-background/80 backdrop-blur">
          <div className="flex items-center gap-3 min-w-0">
            <span className="text-sm font-medium truncate">
              {NAV.find((n) => pathname?.startsWith(n.href))?.label ?? "Overview"}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <BackendBadges />
            <StatusPill />
            <UserMenu />
            <ThemeToggle />
          </div>
        </header>
        <main className="flex-1 overflow-auto">
          <div className="mx-auto max-w-6xl p-6 md:p-8">{children}</div>
        </main>
      </div>
    </div>
  );
}
