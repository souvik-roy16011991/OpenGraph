"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import {
  LayoutGrid,
  MessageSquareText,
  GitBranch,
  GitCompareArrows,
  History as HistoryIcon,
  CircleUser,
  CreditCard,
  CheckCircle2,
  KeyRound,
  Moon,
  Sun,
  Briefcase,
  ChevronDown,
  PanelLeftClose,
  PanelLeftOpen,
  ChevronsLeft,
  ChevronsRight,
  Plus,
} from "lucide-react";
import { useTheme } from "next-themes";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { BrandMark } from "@/components/brand";
import { FlowProgress, isWizardRoute } from "@/components/wizard/flow-progress";
import { UserMenu } from "@/components/user-menu";
import { RouteProgress } from "@/components/route-progress";
import { CommandPalette } from "@/components/command-palette";
import { CreateWorkspaceDialog } from "@/components/workspace/create-workspace-dialog";
import { useWorkspaceStore } from "@/store/workspace-store";
import { useSidebarStore } from "@/store/sidebar-store";

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  matchPrefixes?: string[];
}

const WIZARD_ROUTES = ["/upload", "/domain", "/graph-config", "/build", "/explore"];
// The "My Graphs" nav item stays highlighted both on the list page itself
// (/workspaces) and while the user is stepping through any wizard-edit
// screen for a graph (/upload, /domain, /graph-config, /build, /explore).
const GRAPH_MATCH_PREFIXES = ["/workspaces", ...WIZARD_ROUTES];

const PRIMARY_NAV: NavItem[] = [
  { href: "/templates", label: "Templates", icon: LayoutGrid },
  { href: "/chat", label: "Chat", icon: MessageSquareText },
  { href: "/workspaces", label: "My Graphs", icon: GitBranch, matchPrefixes: GRAPH_MATCH_PREFIXES },
  { href: "/playground", label: "Playground", icon: GitCompareArrows },
  { href: "/history", label: "History", icon: HistoryIcon },
  { href: "/api-keys", label: "API Keys", icon: KeyRound, matchPrefixes: ["/api-keys", "/api-docs"] },
  { href: "/billing", label: "Billing", icon: CreditCard },
  { href: "/profile", label: "Profile", icon: CircleUser },
];

function isActive(pathname: string | null, item: NavItem): boolean {
  if (!pathname) return false;
  if (item.matchPrefixes) {
    return item.matchPrefixes.some((p) => pathname.startsWith(p));
  }
  return pathname.startsWith(item.href);
}

function WorkspaceSwitcher({ collapsed }: { collapsed: boolean }) {
  const router = useRouter();
  const activeId = useWorkspaceStore((s) => s.activeId);
  const setActiveId = useWorkspaceStore((s) => s.setActiveId);
  const [open, setOpen] = React.useState(false);
  const [createOpen, setCreateOpen] = React.useState(false);
  const rootRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const { data } = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.listWorkspaces,
    refetchInterval: 15_000,
  });
  const list = data?.workspaces ?? [];
  const active = list.find((w) => w.id === activeId) ?? null;
  const initial = (active?.name || "·").slice(0, 1).toUpperCase();

  const trigger = collapsed ? (
    <button
      type="button"
      onClick={() => setOpen((o) => !o)}
      className="h-9 w-9 rounded-md border bg-background/60 flex items-center justify-center text-sm font-semibold hover:border-foreground/40 transition-colors"
      aria-label={active ? `Workspace: ${active.name}` : "Select workspace"}
      title={active?.name || "Select workspace"}
    >
      {initial}
    </button>
  ) : (
    <button
      type="button"
      onClick={() => setOpen((o) => !o)}
      className="w-full flex items-center gap-2 rounded-md border bg-background/60 px-3 py-2 text-left hover:border-foreground/40 transition-colors"
    >
      <Briefcase className="h-4 w-4 shrink-0 text-muted-foreground" />
      <span className="flex-1 min-w-0">
        {active ? (
          <>
            <span className="block text-sm font-medium truncate">{active.name}</span>

          </>
        ) : (
          <span className="block text-sm text-muted-foreground">Select workspace…</span>
        )}
      </span>
      <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
    </button>
  );

  return (
    <div className="relative" ref={rootRef}>
      {trigger}
      {open && (
        <div
          className={cn(
            "absolute top-full mt-1 rounded-md border bg-popover shadow-lg z-30 max-h-[320px] overflow-auto",
            "animate-in fade-in-0 zoom-in-95 slide-in-from-top-1 duration-150",
            collapsed ? "left-full ml-2 w-[260px]" : "left-0 right-0",
          )}
        >
          <button
            type="button"
            onClick={() => {
              setOpen(false);
              setCreateOpen(true);
            }}
            className="w-full text-left px-3 py-2 text-sm font-medium text-primary hover:bg-accent transition-colors border-b flex items-center gap-2"
          >
            <Plus className="h-3.5 w-3.5" />
            New workspace
          </button>
          {list.map((w) => (
            <button
              key={w.id}
              onClick={() => {
                setActiveId(w.id);
                setOpen(false);
              }}
              className={cn(
                "w-full text-left px-3 py-2 text-sm hover:bg-accent transition-colors border-b last:border-b-0",
                w.id === activeId && "bg-accent",
              )}
            >
              <span className="flex items-center gap-2">
                {w.id === activeId && <CheckCircle2 className="h-3 w-3 text-emerald-500" />}
                <span className="truncate flex-1">{w.name}</span>

              </span>
            </button>
          ))}
          <Link
            href="/workspaces"
            onClick={() => setOpen(false)}
            className="block w-full text-left px-3 py-2 text-xs text-muted-foreground hover:bg-accent hover:text-foreground border-t"
          >
            Manage workspaces →
          </Link>
        </div>
      )}
      <CreateWorkspaceDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={() => router.push("/upload")}
      />
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

function NavRow({
  item,
  active,
  collapsed,
}: {
  item: NavItem;
  active: boolean;
  collapsed: boolean;
}) {
  const row = (
    <Link
      href={item.href}
      className={cn(
        "relative flex items-center gap-3 rounded-md px-2.5 py-2 text-sm transition-colors",
        active && "bg-accent text-accent-foreground",
        !active && "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
        collapsed && "justify-center px-0",
      )}
      aria-current={active ? "page" : undefined}
    >
      {active && !collapsed && (
        <span
          aria-hidden
          className="absolute left-0 top-1.5 bottom-1.5 w-[2px] rounded-full bg-primary"
        />
      )}
      <item.icon className={cn("h-4 w-4 shrink-0", active && "text-foreground")} />
      {!collapsed && <span className="font-medium truncate">{item.label}</span>}
    </Link>
  );

  if (!collapsed) return row;
  return (
    <Tooltip delayDuration={200}>
      <TooltipTrigger asChild>{row}</TooltipTrigger>
      <TooltipContent side="right">{item.label}</TooltipContent>
    </Tooltip>
  );
}

// Unauthenticated / full-screen routes render without the sidebar chrome so
// the sign-in form isn't stacked next to a broken workspace switcher firing
// 401s. Keep in sync with middleware's auth-page list.
const CHROMELESS_PREFIXES = ["/sign-in", "/sign-up"];

function isChromeless(pathname: string | null): boolean {
  if (!pathname) return false;
  return CHROMELESS_PREFIXES.some((p) => pathname.startsWith(p));
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const collapsed = useSidebarStore((s) => s.collapsed);
  const toggle = useSidebarStore((s) => s.toggle);
  const setCollapsed = useSidebarStore((s) => s.set);

  // On small screens, force the rail to collapsed on mount.
  React.useEffect(() => {
    if (typeof window === "undefined") return;
    const mql = window.matchMedia("(max-width: 767px)");
    if (mql.matches) setCollapsed(true);
  }, [setCollapsed]);

  if (isChromeless(pathname)) {
    return (
      <>
        <RouteProgress />
        {children}
      </>
    );
  }

  const headerLabel =
    PRIMARY_NAV.find((n) => isActive(pathname, n))?.label ?? "Overview";

  return (
    <div className="flex min-h-screen">
      <RouteProgress />
      <CommandPalette />
      {/* Sidebar */}
      <aside
        className={cn(
          "border-r bg-card/40 flex flex-col shrink-0 transition-[width] duration-200 ease-out",
          collapsed ? "w-[60px]" : "w-[240px]",
        )}
      >
        {/* Brand */}
        <Link
          href="/"
          className={cn(
            "h-14 border-b flex items-center gap-2 hover:bg-accent/40 transition-colors",
            collapsed ? "justify-center px-0" : "px-4",
          )}
          title="OpenGraph"
        >
          <div className="h-8 w-8 rounded-md border bg-white flex items-center justify-center shadow-sm shrink-0">
            <BrandMark size={18} />
          </div>
          {!collapsed && (
            <div className="flex flex-col min-w-0">
              <span className="text-sm font-semibold leading-tight truncate">OpenGraph</span>
            </div>
          )}
        </Link>

        {/* Workspace switcher */}
        <div className={cn("border-b", collapsed ? "p-2 flex justify-center" : "p-3")}>
          <WorkspaceSwitcher collapsed={collapsed} />
        </div>

        {/* Primary nav */}
        <nav className={cn("flex flex-col gap-0.5", collapsed ? "p-2" : "p-2")}>
          {PRIMARY_NAV.map((item) => (
            <NavRow
              key={item.href}
              item={item}
              active={isActive(pathname, item)}
              collapsed={collapsed}
            />
          ))}
        </nav>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Collapse toggle */}
        <div className={cn("p-2 border-t", collapsed && "flex justify-center")}>
          <Button
            variant="ghost"
            size="icon"
            onClick={toggle}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="h-8 w-8"
          >
            {collapsed ? <ChevronsRight className="h-4 w-4" /> : <ChevronsLeft className="h-4 w-4" />}
          </Button>
        </div>
      </aside>

      {/* Main pane */}
      <div className="flex flex-col min-w-0 flex-1">
        <header className="h-14 border-b px-4 md:px-6 flex items-center justify-between gap-4 bg-background/80 backdrop-blur sticky top-0 z-20">
          <div className="flex items-center gap-2 min-w-0">
            <Button
              variant="ghost"
              size="icon"
              onClick={toggle}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className="h-8 w-8 md:hidden lg:inline-flex"
            >
              {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
            </Button>
            <span className="text-sm font-medium truncate">{headerLabel}</span>
          </div>
          <div className="flex items-center gap-2 md:gap-3">
            <UserMenu />
            <ThemeToggle />
          </div>
        </header>

        {isWizardRoute(pathname) && <FlowProgress />}

        <main className="flex-1 overflow-auto">
          <div className="mx-auto max-w-7xl p-6 md:p-8">{children}</div>
        </main>
      </div>
    </div>
  );
}
