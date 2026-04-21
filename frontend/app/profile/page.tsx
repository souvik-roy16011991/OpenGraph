"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  Briefcase,
  CircleUser,
  Clock,
  Loader2,
  LogOut,
  MessageSquare,
  Pencil,
  Save,
  X,
} from "lucide-react";
import { api } from "@/lib/api";
import { logout } from "@/lib/auth";
import { errorMessage } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import type { AuditEntry, MeResponse } from "@/lib/schema";

/**
 * User profile page.
 *
 * - Top card: identity + inline display-name editor + rolled-up counts.
 * - Account actions: sign out (the only session-level control in the app).
 * - Below: paginated audit feed filterable by action / workspace.
 *
 * Identity facts come from the backend `/api/v1/me` endpoint — the
 * authoritative `public.users` row synced from Supabase `auth.users`.
 * Display name is editable here; email / password changes go through
 * Supabase (self-service from their hosted flows).
 */

const ACTION_LABELS: Record<string, { label: string; color: string }> = {
  "auth.signup":              { label: "Signed up",              color: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" },
  "workspace.create":         { label: "Created workspace",      color: "bg-sky-500/15 text-sky-700 dark:text-sky-400" },
  "workspace.update":         { label: "Updated workspace",      color: "bg-sky-500/15 text-sky-700 dark:text-sky-400" },
  "workspace.delete":         { label: "Deleted workspace",      color: "bg-rose-500/15 text-rose-700 dark:text-rose-400" },
  "template.instantiate":     { label: "Instantiated template",  color: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-400" },
  "config.domain.update":     { label: "Edited domain config",   color: "bg-amber-500/15 text-amber-700 dark:text-amber-400" },
  "config.graph.update":      { label: "Edited graph config",    color: "bg-amber-500/15 text-amber-700 dark:text-amber-400" },
  "build.start":              { label: "Started build",          color: "bg-violet-500/15 text-violet-700 dark:text-violet-400" },
  "file.upload":              { label: "Uploaded KB files",      color: "bg-teal-500/15 text-teal-700 dark:text-teal-400" },
  "file.delete":              { label: "Deleted file",           color: "bg-rose-500/15 text-rose-700 dark:text-rose-400" },
  "chat.query":               { label: "Asked the agent",        color: "bg-cyan-500/15 text-cyan-700 dark:text-cyan-400" },
  "llm.preference.change":    { label: "Changed LLM preference", color: "bg-fuchsia-500/15 text-fuchsia-700 dark:text-fuchsia-400" },
  "user.profile.update":      { label: "Updated profile",        color: "bg-slate-500/15 text-slate-700 dark:text-slate-400" },
};

function actionMeta(action: string) {
  return ACTION_LABELS[action] ?? { label: action, color: "bg-muted text-muted-foreground" };
}

function initialsFrom(me: MeResponse): string {
  const src = me.display_name || me.email || "?";
  const parts = src.split(/[\s@._-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0][0]?.toUpperCase() ?? "?";
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

function formatRelative(iso: string): string {
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  const s = Math.floor(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const days = Math.floor(h / 24);
  if (days < 30) return `${days}d ago`;
  return d.toLocaleDateString();
}

function ProfileCard({ me }: { me: MeResponse }) {
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState(me.display_name ?? "");
  const qc = useQueryClient();
  const save = useMutation({
    mutationFn: () => api.updateMe({ display_name: draft.trim() || undefined }),
    onSuccess: (fresh) => {
      qc.setQueryData(["me"], fresh);
      setEditing(false);
    },
  });

  React.useEffect(() => {
    setDraft(me.display_name ?? "");
  }, [me.display_name]);

  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-start gap-4">
          <div className="h-14 w-14 rounded-full bg-gradient-to-br from-indigo-500 to-emerald-500 flex items-center justify-center text-white text-lg font-semibold shrink-0">
            {initialsFrom(me)}
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-start justify-between gap-3 flex-wrap">
              <div className="flex-1 min-w-0">
                {!editing ? (
                  <div className="flex items-center gap-2">
                    <h2 className="text-lg font-semibold leading-tight truncate">
                      {me.display_name || me.email || "Anonymous"}
                    </h2>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7"
                      onClick={() => setEditing(true)}
                      aria-label="Edit display name"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <Input
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      placeholder="Display name"
                      className="h-9 max-w-sm"
                      autoFocus
                      onKeyDown={(e) => {
                        if (e.key === "Enter") save.mutate();
                        if (e.key === "Escape") { setEditing(false); setDraft(me.display_name ?? ""); }
                      }}
                    />
                    <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>
                      {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                      Save
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => { setEditing(false); setDraft(me.display_name ?? ""); }}>
                      <X className="h-3.5 w-3.5" /> Cancel
                    </Button>
                  </div>
                )}
                {me.email && (
                  <p className="text-sm text-muted-foreground truncate">{me.email}</p>
                )}
              </div>
            </div>

            <dl className="grid grid-cols-2 sm:grid-cols-3 gap-3 mt-4 text-sm">
              <Stat icon={Briefcase} label="Workspaces" value={me.workspace_count} />
              <Stat icon={Activity}  label="Builds"     value={me.build_count} />
              <Stat icon={MessageSquare} label="Chats"  value={me.chat_count} />
            </dl>

            <p className="text-[11px] text-muted-foreground mt-4 font-mono">
              joined {new Date(me.created_at).toLocaleString()}
            </p>
            {save.isError && (
              <p className="text-xs text-destructive mt-1">Couldn&apos;t save — {errorMessage(save.error)}</p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function AccountActionsCard() {
  const router = useRouter();
  const [pending, setPending] = React.useState(false);

  async function onSignOut() {
    setPending(true);
    try {
      await logout();
      router.replace("/sign-in");
      router.refresh();
    } finally {
      setPending(false);
    }
  }

  return (
    <Card>
      <CardContent className="p-5 sm:p-6">
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">Session</h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              Sign out of this device. Your data stays; you&apos;ll be asked to
              sign in again to access your workspaces.
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={onSignOut}
            disabled={pending}
            className="gap-2 shrink-0"
          >
            {pending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <LogOut className="h-3.5 w-3.5" />
            )}
            {pending ? "Signing out…" : "Sign out"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function Stat({ icon: Icon, label, value }: { icon: React.ComponentType<{className?: string}>; label: string; value: number }) {
  return (
    <div className="rounded-md border px-3 py-2 bg-background/60">
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-muted-foreground font-mono">
        <Icon className="h-3 w-3" /> {label}
      </div>
      <div className="text-lg font-semibold leading-tight mt-0.5">{value.toLocaleString()}</div>
    </div>
  );
}

function AuditRow({ entry }: { entry: AuditEntry }) {
  const meta = actionMeta(entry.action);
  const m = (entry.metadata || {}) as Record<string, unknown>;
  const caption = summarize(entry.action, m);
  return (
    <li className="py-2.5 flex items-start gap-3 border-b last:border-0">
      <span className={`text-[10px] uppercase tracking-wider font-mono px-2 py-0.5 rounded ${meta.color} shrink-0 mt-0.5`}>
        {meta.label}
      </span>
      <div className="flex-1 min-w-0">
        {caption && <p className="text-sm truncate">{caption}</p>}
        <p className="text-[11px] text-muted-foreground font-mono">
          {formatRelative(entry.created_at)}
          {entry.workspace_id && (
            <> · ws {entry.workspace_id.slice(0, 8)}</>
          )}
          {entry.target_id && entry.target_id !== entry.workspace_id && (
            <> · target {String(entry.target_id).slice(0, 16)}</>
          )}
        </p>
      </div>
    </li>
  );
}

function summarize(action: string, m: Record<string, unknown>): string | null {
  switch (action) {
    case "workspace.create":
    case "workspace.delete":
      return m.name ? `"${m.name}"` : null;
    case "workspace.update":
      return Array.isArray(m.fields) ? `fields: ${(m.fields as string[]).join(", ")}` : null;
    case "template.instantiate":
      return m.template_name && m.workspace_name
        ? `${m.template_name} → "${m.workspace_name}"`
        : null;
    case "config.graph.update":
      return Array.isArray(m.changed_sections)
        ? `${(m.changed_sections as string[]).join(", ")}${m.requires_rebuild ? " (requires rebuild)" : ""}`
        : null;
    case "config.domain.update":
      return m.domain_name ? `domain_name: ${m.domain_name}` : null;
    case "build.start":
      return [
        m.skip_embeddings ? "skip_embeddings" : null,
        m.skip_llm_cross_links ? "skip_llm_cross_links" : null,
      ].filter(Boolean).join(" · ") || "full build";
    case "file.upload":
      return `${m.knowledge_added ?? 0} knowledge + ${m.tool_added ?? 0} tool${(m.duplicates as number) ? `, ${m.duplicates} dupes` : ""}`;
    case "chat.query":
      return typeof m.query_preview === "string" ? m.query_preview : null;
    case "llm.preference.change":
      return m.model ? `model: ${m.model}` : "cleared preference";
    default:
      return null;
  }
}

export default function ProfilePage() {
  const [actionFilter, setActionFilter] = React.useState<string>("");
  const meQuery = useQuery({ queryKey: ["me"], queryFn: api.me });
  const auditQuery = useQuery({
    queryKey: ["me", "audit", actionFilter],
    queryFn: () => api.myAudit({ limit: 50, action: actionFilter || undefined }),
  });

  const me = meQuery.data;
  const audit = auditQuery.data;
  const actionsSeen = React.useMemo(() => {
    const s = new Set<string>();
    (audit?.entries || []).forEach((e) => s.add(e.action));
    return Array.from(s).sort();
  }, [audit]);

  if (meQuery.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading profile…</p>;
  }
  if (!me) {
    return <p className="text-sm text-destructive">{meQuery.error ? errorMessage(meQuery.error) : "No profile data."}</p>;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
          <CircleUser className="h-5 w-5" /> Your profile
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Identity, account stats, and a feed of everything you've done across
          your workspaces.
        </p>
      </div>

      <ProfileCard me={me} />

      <AccountActionsCard />

      <Card>
        <CardContent className="p-4 sm:p-5">
          <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
            <h2 className="font-medium flex items-center gap-2">
              <Clock className="h-4 w-4" /> Audit trail
            </h2>
            <div className="flex items-center gap-2">
              <select
                value={actionFilter}
                onChange={(e) => setActionFilter(e.target.value)}
                className="h-8 rounded-md border border-input bg-background px-2 text-xs"
              >
                <option value="">All actions</option>
                {actionsSeen.map((a) => (
                  <option key={a} value={a}>{actionMeta(a).label}</option>
                ))}
              </select>
            </div>
          </div>

          {auditQuery.isLoading ? (
            <p className="text-sm text-muted-foreground py-4">Loading audit feed…</p>
          ) : !audit || audit.entries.length === 0 ? (
            <p className="text-sm text-muted-foreground py-6 text-center">
              No activity yet. Create a workspace or start a build to see events here.
            </p>
          ) : (
            <ul className="divide-y">
              {audit.entries.map((e) => <AuditRow key={e.id} entry={e} />)}
            </ul>
          )}

          {audit?.has_more && (
            <p className="text-[11px] text-muted-foreground mt-3 text-center">
              Showing newest 50 events — older events are retained but not loaded here.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
