"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  LayoutGrid,
  MessageSquareText,
  GitBranch,
  GitCompareArrows,
  History as HistoryIcon,
  CircleUser,
  CreditCard,
  Plus,
  Search,
  ArrowRight,
  Briefcase,
} from "lucide-react";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { CreateWorkspaceDialog } from "@/components/workspace/create-workspace-dialog";
import { api } from "@/lib/api";
import { useWorkspaceStore } from "@/store/workspace-store";
import { cn } from "@/lib/utils";

type Command = {
  id: string;
  label: string;
  hint?: string;
  icon: React.ComponentType<{ className?: string }>;
  group: "Actions" | "Workspaces" | "Go to";
  keywords?: string;
  run: () => void;
};

export function CommandPalette() {
  const router = useRouter();
  const setActiveId = useWorkspaceStore((s) => s.setActiveId);
  const activeId = useWorkspaceStore((s) => s.activeId);

  const [open, setOpen] = React.useState(false);
  const [createOpen, setCreateOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const [cursor, setCursor] = React.useState(0);
  const listRef = React.useRef<HTMLDivElement>(null);

  const workspacesQuery = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.listWorkspaces,
    enabled: open,
  });

  // Global Cmd/Ctrl+K toggle.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  React.useEffect(() => {
    if (!open) {
      setQuery("");
      setCursor(0);
    }
  }, [open]);

  const commands = React.useMemo<Command[]>(() => {
    const ws = workspacesQuery.data?.workspaces ?? [];
    const go = (href: string) => () => {
      setOpen(false);
      router.push(href);
    };
    const base: Command[] = [
      {
        id: "new-workspace",
        label: "New workspace",
        hint: "Create a fresh graph",
        icon: Plus,
        group: "Actions",
        keywords: "create add graph",
        run: () => {
          setOpen(false);
          setCreateOpen(true);
        },
      },
      { id: "go-templates", label: "Templates", icon: LayoutGrid, group: "Go to", run: go("/templates") },
      { id: "go-chat", label: "Chat", icon: MessageSquareText, group: "Go to", run: go("/chat") },
      { id: "go-workspaces", label: "My graphs", icon: GitBranch, group: "Go to", run: go("/workspaces") },
      { id: "go-playground", label: "Playground", icon: GitCompareArrows, group: "Go to", run: go("/playground") },
      { id: "go-history", label: "History", icon: HistoryIcon, group: "Go to", run: go("/history") },
      { id: "go-billing", label: "Billing", icon: CreditCard, group: "Go to", run: go("/billing") },
      { id: "go-profile", label: "Profile", icon: CircleUser, group: "Go to", run: go("/profile") },
    ];

    const wsCommands: Command[] = ws.map((w) => ({
      id: `ws-${w.id}`,
      label: w.name,
      hint: w.id === activeId ? "Current workspace" : "Switch to workspace",
      icon: Briefcase,
      group: "Workspaces",
      keywords: `${w.description ?? ""} ${w.id}`,
      run: () => {
        setOpen(false);
        setActiveId(w.id);
        if (w.last_build_status === "done") router.push("/explore");
        else router.push("/upload");
      },
    }));

    return [...base, ...wsCommands];
  }, [workspacesQuery.data, activeId, router, setActiveId]);

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter((c) => {
      const hay = `${c.label} ${c.hint ?? ""} ${c.keywords ?? ""} ${c.group}`.toLowerCase();
      return q.split(/\s+/).every((tok) => hay.includes(tok));
    });
  }, [commands, query]);

  React.useEffect(() => {
    setCursor(0);
  }, [query]);

  React.useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(`[data-cursor="${cursor}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  const grouped = React.useMemo(() => {
    const groups: Record<string, Command[]> = {};
    for (const c of filtered) {
      (groups[c.group] ||= []).push(c);
    }
    return groups;
  }, [filtered]);

  let runningIndex = 0;

  return (
    <>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="p-0 gap-0 max-w-xl overflow-hidden">
          <DialogTitle className="sr-only">Command palette</DialogTitle>
          <div className="flex items-center gap-2 border-b px-3">
            <Search className="h-4 w-4 text-muted-foreground shrink-0" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown") {
                  e.preventDefault();
                  setCursor((c) => Math.min(c + 1, filtered.length - 1));
                } else if (e.key === "ArrowUp") {
                  e.preventDefault();
                  setCursor((c) => Math.max(c - 1, 0));
                } else if (e.key === "Enter") {
                  e.preventDefault();
                  filtered[cursor]?.run();
                } else if (e.key === "Escape") {
                  setOpen(false);
                }
              }}
              placeholder="Search actions, workspaces, pages…"
              className="flex-1 h-11 bg-transparent outline-none text-sm placeholder:text-muted-foreground"
            />
            <kbd className="hidden sm:inline-flex text-[10px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded border">
              ESC
            </kbd>
          </div>
          <div
            ref={listRef}
            className="max-h-[360px] overflow-y-auto py-1"
            onMouseLeave={() => setCursor((c) => c)}
          >
            {filtered.length === 0 ? (
              <p className="px-4 py-8 text-center text-sm text-muted-foreground">
                No matches. Try a different search.
              </p>
            ) : (
              Object.entries(grouped).map(([group, items]) => (
                <div key={group} className="py-1">
                  <p className="px-3 pt-1.5 pb-1 text-[10px] uppercase tracking-wider text-muted-foreground/70 font-medium">
                    {group}
                  </p>
                  {items.map((c) => {
                    const idx = runningIndex++;
                    const selected = idx === cursor;
                    const Icon = c.icon;
                    return (
                      <button
                        key={c.id}
                        data-cursor={idx}
                        onMouseEnter={() => setCursor(idx)}
                        onClick={() => c.run()}
                        className={cn(
                          "w-full flex items-center gap-3 px-3 py-2 text-sm text-left transition-colors",
                          selected ? "bg-accent" : "hover:bg-accent/60",
                        )}
                      >
                        <Icon className="h-4 w-4 text-muted-foreground shrink-0" />
                        <span className="flex-1 min-w-0 truncate">{c.label}</span>
                        {c.hint && (
                          <span className="text-xs text-muted-foreground truncate">{c.hint}</span>
                        )}
                        {selected && <ArrowRight className="h-3.5 w-3.5 text-muted-foreground" />}
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </div>
          <div className="border-t px-3 py-2 text-[10px] text-muted-foreground flex items-center gap-3">
            <span className="flex items-center gap-1">
              <kbd className="bg-muted px-1 py-0.5 rounded border">↑</kbd>
              <kbd className="bg-muted px-1 py-0.5 rounded border">↓</kbd>
              navigate
            </span>
            <span className="flex items-center gap-1">
              <kbd className="bg-muted px-1 py-0.5 rounded border">↵</kbd>
              select
            </span>
            <span className="flex items-center gap-1 ml-auto">
              <kbd className="bg-muted px-1 py-0.5 rounded border">⌘</kbd>
              <kbd className="bg-muted px-1 py-0.5 rounded border">K</kbd>
              toggle
            </span>
          </div>
        </DialogContent>
      </Dialog>
      <CreateWorkspaceDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={() => router.push("/upload")}
      />
    </>
  );
}
