"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  ExternalLink,
  KeyRound,
  Package,
  Plus,
  ShieldCheck,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { CodeSnippet } from "@/components/api-docs/code-snippet";
import { api } from "@/lib/api";

const DEFAULT_KEY_PLACEHOLDER = "og_live_<your key>";
const DEFAULT_WS_PLACEHOLDER = "<workspace-uuid>";

type Tab = "curl" | "python" | "typescript" | "node";

export default function ApiDocsPage() {
  const base = typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "https://api.opengraph.example";

  const keysQuery = useQuery({ queryKey: ["api-keys"], queryFn: api.listApiKeys });
  const workspacesQuery = useQuery({
    queryKey: ["workspaces"],
    queryFn: api.listWorkspaces,
  });

  const firstLiveKey = (keysQuery.data ?? []).find((k) => !k.revoked_at);
  const firstWorkspace = workspacesQuery.data?.workspaces?.[0];

  // Keys aren't retrievable in plaintext post-creation. The "working example"
  // therefore substitutes the prefix only (``og_live_abcd1234…``) so the
  // snippet reads correctly; the user pastes their own secret when running.
  const keyExample = firstLiveKey ? `${firstLiveKey.prefix}…` : DEFAULT_KEY_PLACEHOLDER;
  const wsExample = firstWorkspace?.id ?? DEFAULT_WS_PLACEHOLDER;

  const [tab, setTab] = React.useState<Tab>("curl");

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <BookOpen className="h-5 w-5" /> API documentation
          </h1>
          <p className="text-muted-foreground text-sm mt-1">
            Call your knowledge graphs from any language. Public surface at{" "}
            <code className="font-mono">/api/v1/ext/*</code>, authenticated with{" "}
            <Link href="/api-keys" className="underline hover:text-foreground">
              API keys
            </Link>
            . Full OpenAPI spec:{" "}
            <a
              href={`${base}/api/v1/ext/openapi.json`}
              target="_blank"
              rel="noreferrer"
              className="underline hover:text-foreground"
            >
              /api/v1/ext/openapi.json <ExternalLink className="inline h-3 w-3 align-[-1px]" />
            </a>
          </p>
        </div>
        <div className="flex gap-2 flex-wrap">
          <Button asChild variant="outline">
            <Link href="/api-keys" className="gap-2">
              <KeyRound className="h-4 w-4" /> Manage keys
            </Link>
          </Button>
          {!firstLiveKey && (
            <Button asChild>
              <Link href="/api-keys" className="gap-2">
                <Plus className="h-4 w-4" /> Create your first key
              </Link>
            </Button>
          )}
        </div>
      </div>

      <SetupCards />

      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <h2 className="text-lg font-semibold tracking-tight flex items-center gap-2">
            <Zap className="h-4 w-4" /> Query a graph
          </h2>
          <div className="flex items-center gap-1.5 flex-wrap">
            {(["curl", "python", "typescript", "node"] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`px-2.5 py-1 rounded-full text-xs border transition-colors ${
                  tab === t
                    ? "bg-foreground text-background border-foreground"
                    : "bg-card hover:bg-accent/60"
                }`}
              >
                {t === "typescript" ? "TypeScript" : t === "node" ? "Node" : t}
              </button>
            ))}
          </div>
        </div>

        <p className="text-xs text-muted-foreground">
          Ask a natural-language question. The assistant replies as markdown.{" "}
          {firstLiveKey ? (
            <>
              Using key <code className="font-mono">{firstLiveKey.prefix}…</code>
              {firstWorkspace ? (
                <>
                  {" "}and workspace{" "}
                  <code className="font-mono">{firstWorkspace.name}</code>.
                </>
              ) : null}
            </>
          ) : (
            <>Replace <code>{DEFAULT_KEY_PLACEHOLDER}</code> with your real key.</>
          )}
        </p>

        {tab === "curl" && (
          <CodeSnippet
            lang="curl"
            code={`curl -X POST ${base}/api/v1/ext/query \\
  -H "Authorization: Bearer ${keyExample}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "workspace_id": "${wsExample}",
    "query": "What are the tools used in the payments pipeline?"
  }'`}
          />
        )}
        {tab === "python" && (
          <CodeSnippet
            lang="python"
            code={`# pip install opengraph-sdk
from opengraph_sdk import Client

client = Client(
    api_key="${keyExample}",
    base_url="${base}",
)

resp = client.query(
    workspace_id="${wsExample}",
    query="What are the tools used in the payments pipeline?",
)
print(resp.response)
print("follow-ups:", resp.follow_up_suggestions)`}
          />
        )}
        {tab === "typescript" && (
          <CodeSnippet
            lang="typescript"
            code={`// npm install @opengraph/sdk
import { OpenGraphClient } from "@opengraph/sdk";

const client = new OpenGraphClient({
  apiKey: "${keyExample}",
  baseUrl: "${base}",
});

const resp = await client.query({
  workspaceId: "${wsExample}",
  query: "What are the tools used in the payments pipeline?",
});

console.log(resp.response);
console.log("follow-ups:", resp.followUpSuggestions);`}
          />
        )}
        {tab === "node" && (
          <CodeSnippet
            lang="node"
            code={`// Node >= 18, no extra deps
const res = await fetch("${base}/api/v1/ext/query", {
  method: "POST",
  headers: {
    "Authorization": "Bearer ${keyExample}",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    workspace_id: "${wsExample}",
    query: "What are the tools used in the payments pipeline?",
  }),
});
if (!res.ok) throw new Error(\`Query failed: \${res.status}\`);
const data = await res.json();
console.log(data.response);`}
          />
        )}
      </section>

      <EndpointGrid base={base} keyExample={keyExample} wsExample={wsExample} />

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <ShieldCheck className="h-4 w-4" /> Conventions
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm text-muted-foreground">
          <p>
            <strong className="text-foreground">Auth.</strong>{" "}
            Every request carries{" "}
            <code className="font-mono">Authorization: Bearer og_live_…</code>. Invalid
            or revoked keys return <code>401</code>.
          </p>
          <p>
            <strong className="text-foreground">Workspace scope.</strong>{" "}
            Account-scoped keys require a <code>workspace_id</code> on every call;
            workspace-pinned keys may omit it. Mismatches return <code>404</code> rather than{" "}
            <code>403</code>.
          </p>
          <p>
            <strong className="text-foreground">Rate limits.</strong>{" "}
            Every response carries <code>X-RateLimit-Limit</code>,{" "}
            <code>X-RateLimit-Remaining</code>, and <code>X-RateLimit-Reset</code> headers.
            Over-quota returns <code>429</code> with <code>Retry-After</code>.
          </p>
          <p>
            <strong className="text-foreground">Errors.</strong>{" "}
            All error responses are JSON with at least a{" "}
            <code className="font-mono">detail</code> field. Network errors and{" "}
            <code>5xx</code> are safe to retry with exponential backoff.
          </p>
          <p>
            <strong className="text-foreground">Versioning.</strong>{" "}
            Path is <code>/api/v1/ext/*</code>. Response shape is stable within v1 —
            fields may be added, never removed or retyped, without a version bump.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function SetupCards() {
  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <KeyRound className="h-4 w-4" /> 1. Mint a key
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground space-y-2">
          <p>
            Go to <Link href="/api-keys" className="underline hover:text-foreground">/api-keys</Link>{" "}
            and click <strong>New key</strong>. The plaintext is shown once — copy it
            somewhere safe.
          </p>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Package className="h-4 w-4" /> 2. Install an SDK
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground space-y-2">
          <p>
            Official SDKs handle auth, retries, and typed responses.
          </p>
          <div className="flex gap-1.5 flex-wrap">
            <Badge variant="outline" className="font-mono text-[10px]">pip install opengraph-sdk</Badge>
            <Badge variant="outline" className="font-mono text-[10px]">npm i @opengraph/sdk</Badge>
          </div>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <Zap className="h-4 w-4" /> 3. Query the graph
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground space-y-2">
          <p>
            Pass <code>workspace_id</code> + <code>query</code> and get back a markdown
            answer with follow-up suggestions and token usage.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function EndpointGrid({
  base,
  keyExample,
  wsExample,
}: {
  base: string;
  keyExample: string;
  wsExample: string;
}) {
  const rows = [
    { method: "POST",   path: "/api/v1/ext/query",                  desc: "Ask a natural-language question" },
    { method: "GET",    path: "/api/v1/ext/graph/stats",            desc: "Node + edge counts by type" },
    { method: "GET",    path: "/api/v1/ext/graph/search",           desc: "Hybrid keyword + semantic search" },
    { method: "GET",    path: "/api/v1/ext/graph/node/{id}",        desc: "Node + edges + children + parent" },
    { method: "POST",   path: "/api/v1/ext/graph/traverse",         desc: "BFS traverse with edge filter" },
    { method: "GET",    path: "/api/v1/ext/graph/tree",             desc: "Hierarchical navigation tree" },
    { method: "GET",    path: "/api/v1/ext/graph/tools",            desc: "All extracted Tool nodes" },
    { method: "GET",    path: "/api/v1/ext/graph/chapters",         desc: "Flat list of chapters" },
    { method: "GET",    path: "/api/v1/ext/history/chats",          desc: "Recent chat sessions" },
    { method: "GET",    path: "/api/v1/ext/history/chats/{id}",     desc: "Full message thread" },
    { method: "GET",    path: "/api/v1/ext/history/builds",         desc: "Recent build jobs" },
    { method: "GET",    path: "/api/v1/ext/history/builds/{id}",    desc: "Single build status + stats" },
  ];

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold tracking-tight">Endpoints</h2>
      <div className="rounded-md border overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-xs text-muted-foreground">
            <tr>
              <th className="text-left font-medium px-3 py-2 w-20">Method</th>
              <th className="text-left font-medium px-3 py-2">Path</th>
              <th className="text-left font-medium px-3 py-2 hidden md:table-cell">Description</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.path} className="border-t">
                <td className="px-3 py-2">
                  <Badge
                    variant="outline"
                    className={`text-[10px] font-mono ${
                      r.method === "POST" ? "text-emerald-700 dark:text-emerald-400" : "text-sky-700 dark:text-sky-400"
                    }`}
                  >
                    {r.method}
                  </Badge>
                </td>
                <td className="px-3 py-2 font-mono text-xs">{r.path}</td>
                <td className="px-3 py-2 text-muted-foreground hidden md:table-cell">{r.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">
        Full reference with request/response schemas lives in the{" "}
        <a
          href={`${base}/docs`}
          target="_blank"
          rel="noreferrer"
          className="underline hover:text-foreground"
        >
          Swagger UI <ExternalLink className="inline h-3 w-3 align-[-1px]" />
        </a>
        .
      </p>
    </section>
  );
}
