"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, KeyRound, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { CodeSnippet } from "@/components/api-docs/code-snippet";
import { api, publicApiBaseUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

const DEFAULT_KEY_PLACEHOLDER = "og_live_<your key>";
const DEFAULT_WS_PLACEHOLDER = "<workspace-uuid>";

type Tab = "curl" | "python" | "typescript" | "node";

/**
 * Developer docs landing — compact single-column read.
 *
 * The previous layout stacked three setup cards + a boxed conventions
 * panel, which felt like a checklist form. The page is really just
 * "here's the URL, here's a snippet, here's the endpoint list" — so we
 * render exactly that, top to bottom, without wrapping each section in
 * a shadcn Card. Cards here added weight without adding information.
 */
export default function ApiDocsPage() {
  // Resolved client-side from NEXT_PUBLIC_API_BASE so snippets show the
  // actual backend URL the caller will hit (prod: opengraph-backend
  // .onrender.com; local: localhost:8000). See note in
  // components/workspace/deploy-dialog.tsx — same fix.
  const base = publicApiBaseUrl();

  const keysQuery = useQuery({ queryKey: ["api-keys"], queryFn: api.listApiKeys });
  const workspacesQuery = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.listWorkspaces,
  });

  const firstLiveKey = (keysQuery.data ?? []).find((k) => !k.revoked_at);
  const firstWorkspace = workspacesQuery.data?.workspaces?.[0];

  const keyExample = firstLiveKey ? `${firstLiveKey.prefix}…` : DEFAULT_KEY_PLACEHOLDER;
  const wsExample = firstWorkspace?.id ?? DEFAULT_WS_PLACEHOLDER;

  const [tab, setTab] = React.useState<Tab>("curl");

  return (
    <div className="space-y-8 max-w-3xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">API</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Public surface at <code className="font-mono">/api/v1/ext/*</code>.
            Authenticated with an API key — mint one at{" "}
            <Link href="/api-keys" className="underline hover:text-foreground">
              /api-keys
            </Link>
            .
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {firstLiveKey ? (
            <Button asChild variant="outline" size="sm">
              <Link href="/api-keys" className="gap-1.5">
                <KeyRound className="h-3.5 w-3.5" /> Manage keys
              </Link>
            </Button>
          ) : (
            <Button asChild size="sm">
              <Link href="/api-keys" className="gap-1.5">
                <Plus className="h-3.5 w-3.5" /> Create your first key
              </Link>
            </Button>
          )}
        </div>
      </div>

      {/* Install + quick-start — one inline block, no card chrome */}
      <section className="space-y-3">
        <SectionHeading>Install</SectionHeading>
        <div className="flex flex-wrap gap-2">
          <Badge variant="outline" className="font-mono text-[11px] h-6 px-2">
            pip install opengraph-sdk
          </Badge>
          <Badge variant="outline" className="font-mono text-[11px] h-6 px-2">
            npm i @opengraph/sdk
          </Badge>
        </div>
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <SectionHeading>Query a graph</SectionHeading>
          <LangTabs value={tab} onChange={setTab} />
        </div>
        <p className="text-xs text-muted-foreground">
          {firstLiveKey ? (
            <>
              Using <code className="font-mono">{firstLiveKey.prefix}…</code>
              {firstWorkspace && (
                <>
                  {" · "}workspace <code className="font-mono">{firstWorkspace.name}</code>
                </>
              )}
              . Replace the ellipsis with the full secret from{" "}
              <Link href="/api-keys" className="underline hover:text-foreground">
                /api-keys
              </Link>
              .
            </>
          ) : (
            <>
              Replace <code>{DEFAULT_KEY_PLACEHOLDER}</code> with a real key
              before running.
            </>
          )}
        </p>
        <CodeSnippet
          lang={tab === "node" ? "node" : tab === "typescript" ? "typescript" : tab === "python" ? "python" : "curl"}
          code={buildQuerySnippet(tab, base, keyExample, wsExample)}
        />
      </section>

      <section className="space-y-3">
        <SectionHeading>Endpoints</SectionHeading>
        <EndpointTable />
        <p className="text-xs text-muted-foreground">
          Full request / response schemas:{" "}
          <a
            href={`${base}/api/v1/ext/openapi.json`}
            target="_blank"
            rel="noreferrer"
            className="underline hover:text-foreground inline-flex items-center gap-1"
          >
            OpenAPI spec <ExternalLink className="h-3 w-3" />
          </a>
          {" · "}
          <a
            href={`${base}/docs`}
            target="_blank"
            rel="noreferrer"
            className="underline hover:text-foreground inline-flex items-center gap-1"
          >
            Swagger UI <ExternalLink className="h-3 w-3" />
          </a>
        </p>
      </section>

      <section className="space-y-3">
        <SectionHeading>Conventions</SectionHeading>
        <dl className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3 text-sm">
          <Convention
            term="Auth"
            def={<>Bearer API key in <code className="font-mono">Authorization</code>. Invalid or revoked → <code>401</code>.</>}
          />
          <Convention
            term="Workspace scope"
            def={<>Account-scoped keys pass <code>workspace_id</code> every call. Pinned keys may omit it. Mismatch → <code>404</code>.</>}
          />
          <Convention
            term="Rate limits"
            def={<>Every response carries <code>X-RateLimit-*</code> headers. Over-quota → <code>429</code> with <code>Retry-After</code>.</>}
          />
          <Convention
            term="Errors"
            def={<>JSON with a <code className="font-mono">detail</code> field. <code>5xx</code> + network errors are retry-safe with backoff.</>}
          />
          <Convention
            term="Versioning"
            def={<>Path is <code>/api/v1/ext/*</code>. Fields may be added in v1; never removed or retyped without a major bump.</>}
          />
        </dl>
      </section>
    </div>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return <h2 className="text-[11px] uppercase tracking-wider font-medium text-muted-foreground">{children}</h2>;
}

function LangTabs({ value, onChange }: { value: Tab; onChange: (t: Tab) => void }) {
  return (
    <div className="flex items-center gap-1 flex-wrap">
      {(["curl", "python", "typescript", "node"] as Tab[]).map((t) => (
        <button
          key={t}
          onClick={() => onChange(t)}
          className={cn(
            "px-2.5 py-1 rounded-full text-[11px] border transition-colors",
            value === t
              ? "bg-foreground text-background border-foreground"
              : "bg-card hover:bg-accent/60",
          )}
        >
          {t === "typescript" ? "TypeScript" : t === "node" ? "Node" : t}
        </button>
      ))}
    </div>
  );
}

function Convention({ term, def }: { term: string; def: React.ReactNode }) {
  return (
    <div>
      <dt className="text-foreground font-medium">{term}</dt>
      <dd className="text-muted-foreground mt-0.5">{def}</dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Endpoint table — thin, uses method colour as the primary differentiator.
// ---------------------------------------------------------------------------

const ENDPOINTS: Array<{ method: "GET" | "POST"; path: string; desc: string }> = [
  { method: "POST", path: "/api/v1/ext/query",               desc: "Ask a natural-language question" },
  { method: "GET",  path: "/api/v1/ext/graph/stats",         desc: "Node + edge counts by type" },
  { method: "GET",  path: "/api/v1/ext/graph/search",        desc: "Hybrid keyword + semantic search" },
  { method: "GET",  path: "/api/v1/ext/graph/node/{id}",     desc: "Node + edges + children + parent" },
  { method: "POST", path: "/api/v1/ext/graph/traverse",      desc: "BFS traverse with edge filter" },
  { method: "GET",  path: "/api/v1/ext/graph/tree",          desc: "Hierarchical navigation tree" },
  { method: "GET",  path: "/api/v1/ext/graph/tools",         desc: "All extracted Tool nodes" },
  { method: "GET",  path: "/api/v1/ext/graph/chapters",      desc: "Flat list of chapters" },
  { method: "GET",  path: "/api/v1/ext/history/chats",       desc: "Recent chat sessions" },
  { method: "GET",  path: "/api/v1/ext/history/chats/{id}",  desc: "Full message thread" },
  { method: "GET",  path: "/api/v1/ext/history/builds",      desc: "Recent build jobs" },
  { method: "GET",  path: "/api/v1/ext/history/builds/{id}", desc: "Single build status + stats" },
];

function EndpointTable() {
  return (
    <div className="rounded-md border divide-y">
      {ENDPOINTS.map((r) => (
        <div
          key={r.path}
          className="grid grid-cols-[54px_1fr] md:grid-cols-[54px_minmax(260px,1fr)_2fr] items-center gap-3 px-3 py-2 text-xs hover:bg-accent/30 transition-colors"
        >
          <span
            className={cn(
              "font-mono font-semibold text-[10px] tracking-wider",
              r.method === "POST" ? "text-emerald-700 dark:text-emerald-400" : "text-sky-700 dark:text-sky-400",
            )}
          >
            {r.method}
          </span>
          <span className="font-mono truncate" title={r.path}>{r.path}</span>
          <span className="text-muted-foreground hidden md:block truncate">{r.desc}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Snippet builder
// ---------------------------------------------------------------------------

function buildQuerySnippet(lang: Tab, base: string, key: string, ws: string): string {
  const q = "What are the tools used in the payments pipeline?";
  if (lang === "curl") {
    return `curl -X POST ${base}/api/v1/ext/query \\
  -H "Authorization: Bearer ${key}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "workspace_id": "${ws}",
    "query": "${q}"
  }'`;
  }
  if (lang === "python") {
    return `# pip install opengraph-sdk
from opengraph_sdk import Client

client = Client(api_key="${key}", base_url="${base}")

resp = client.query(
    workspace_id="${ws}",
    query="${q}",
)
print(resp.response)
print("follow-ups:", resp.follow_up_suggestions)`;
  }
  if (lang === "typescript") {
    return `// npm install @opengraph/sdk
import { OpenGraphClient } from "@opengraph/sdk";

const client = new OpenGraphClient({
  apiKey: "${key}",
  baseUrl: "${base}",
});

const resp = await client.query({
  workspaceId: "${ws}",
  query: "${q}",
});
console.log(resp.response);`;
  }
  // node
  return `// Node >= 18, no extra deps
const res = await fetch("${base}/api/v1/ext/query", {
  method: "POST",
  headers: {
    "Authorization": "Bearer ${key}",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    workspace_id: "${ws}",
    query: "${q}",
  }),
});
if (!res.ok) throw new Error(\`Query failed: \${res.status}\`);
const data = await res.json();
console.log(data.response);`;
}
