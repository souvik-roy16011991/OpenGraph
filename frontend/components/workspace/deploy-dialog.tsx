"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  AlertCircle,
  CheckCircle2,
  Copy,
  Check,
  ExternalLink,
  KeyRound,
  Plus,
  Rocket,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { CodeSnippet } from "@/components/api-docs/code-snippet";
import { api } from "@/lib/api";
import type { ApiKeyRow, DeployResponse, WorkspaceSummary } from "@/lib/schema";
import { cn } from "@/lib/utils";

type Language = "python" | "typescript" | "curl";

type Props = {
  workspace: WorkspaceSummary | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

/**
 * Two-step deploy.
 *
 *   Step 1 — Configure: confirm language, review build stats, click Deploy.
 *   Step 2 — Success:   deployment facts (endpoint/IDs) + an optional API-key
 *                       picker. Snippet uses the picked key's prefix OR a
 *                       clear ``og_live_YOUR_KEY`` placeholder. Never shows
 *                       a plaintext secret — that lives only at /api-keys.
 *
 * Rationale for the split: credentials should be managed separately from
 * deployments. A user can rotate keys without redeploying, share one
 * account-scoped key across every graph, and revoke + replace a leaked
 * key without touching deployment state.
 */
export function DeployDialog({ workspace, open, onOpenChange }: Props) {
  const qc = useQueryClient();
  const [language, setLanguage] = React.useState<Language>("python");
  const [result, setResult] = React.useState<DeployResponse | null>(null);
  const [selectedKeyId, setSelectedKeyId] = React.useState<string | null>(null);

  const base = typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "https://api.opengraph.example";
  const endpoint = `${base}/api/v1/ext/query`;

  const reset = React.useCallback(() => {
    setResult(null);
    setSelectedKeyId(null);
  }, []);

  const handleClose = (v: boolean) => {
    onOpenChange(v);
    if (!v) setTimeout(reset, 250);
  };

  const deployMut = useMutation({
    mutationFn: (ws_id: string) => api.deployWorkspace(ws_id),
    onSuccess: (data) => {
      setResult(data);
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      toast.success(
        data.first_deploy ? "Deployed — API is live" : "Redeployed",
      );
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const canDeploy = workspace?.last_build_status === "done";
  const stats = workspace?.stats ?? {};
  const nodeCount = (stats as { total_nodes?: number }).total_nodes ?? 0;
  const edgeCount = (stats as { total_edges?: number }).total_edges ?? 0;

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      {/* The SuccessStep stacks ~6 sections (header, deployment facts, key
       *  picker, language tabs, code snippet, footer) — on laptop-sized
       *  viewports this exceeds the viewport and clips the Done button.
       *  Cap height at 90vh and allow internal scroll so the full flow is
       *  always reachable. Other dialogs in the app fit comfortably and
       *  don't need the cap — kept scoped here. */}
      <DialogContent className="max-w-xl max-h-[90vh] overflow-y-auto">
        {result === null ? (
          <ConfigureStep
            workspace={workspace}
            canDeploy={canDeploy}
            nodeCount={nodeCount}
            edgeCount={edgeCount}
            language={language}
            onLanguageChange={setLanguage}
            onDeploy={() => workspace && deployMut.mutate(workspace.id)}
            onCancel={() => handleClose(false)}
            pending={deployMut.isPending}
          />
        ) : (
          <SuccessStep
            workspace={workspace}
            result={result}
            language={language}
            onLanguageChange={setLanguage}
            endpoint={endpoint}
            base={base}
            selectedKeyId={selectedKeyId}
            onSelectedKeyChange={setSelectedKeyId}
            onClose={() => handleClose(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Step 1 — Configure
// ---------------------------------------------------------------------------

function ConfigureStep({
  workspace,
  canDeploy,
  nodeCount,
  edgeCount,
  language,
  onLanguageChange,
  onDeploy,
  onCancel,
  pending,
}: {
  workspace: WorkspaceSummary | null;
  canDeploy: boolean;
  nodeCount: number;
  edgeCount: number;
  language: Language;
  onLanguageChange: (l: Language) => void;
  onDeploy: () => void;
  onCancel: () => void;
  pending: boolean;
}) {
  const alreadyDeployed = Boolean(workspace?.deployed_at);
  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          <Rocket className="h-4 w-4" />
          {alreadyDeployed ? "Redeploy" : "Deploy"} &ldquo;{workspace?.name}&rdquo;
        </DialogTitle>
        <DialogDescription>
          Publish this graph to <code className="font-mono">/api/v1/ext/*</code>.
          You&apos;ll need an API key to actually call it — mint one at{" "}
          <Link
            href="/api-keys"
            className="underline hover:text-foreground"
            target="_blank"
          >
            /api-keys
          </Link>
          {" "}any time (before or after deploy).
        </DialogDescription>
      </DialogHeader>

      {!canDeploy && (
        <div className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs">
          <AlertCircle className="h-4 w-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
          <div>
            <p className="font-medium text-amber-900 dark:text-amber-200">
              Build your graph first
            </p>
            <p className="text-amber-800/80 dark:text-amber-300/80">
              Deploy needs at least one successful build. Open the graph and
              click Build, then come back here.
            </p>
          </div>
        </div>
      )}

      <div className="space-y-3">
        <div>
          <p className="text-xs uppercase tracking-wider text-muted-foreground mb-1.5">
            Language
          </p>
          <div className="flex items-center gap-1.5 flex-wrap">
            {(["python", "typescript", "curl"] as Language[]).map((l) => (
              <button
                key={l}
                type="button"
                onClick={() => onLanguageChange(l)}
                className={cn(
                  "px-3 py-1.5 rounded-full text-xs border transition-colors",
                  language === l
                    ? "bg-foreground text-background border-foreground"
                    : "bg-card hover:bg-accent/60",
                )}
              >
                {l === "typescript" ? "TypeScript" : l === "python" ? "Python" : "curl"}
              </button>
            ))}
          </div>
        </div>

        <div className="rounded-md border bg-muted/30 px-3 py-2.5 space-y-1 text-xs">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Graph</span>
            <span className="font-mono">
              {nodeCount} nodes · {edgeCount} edges
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Last build</span>
            <span className="font-mono">
              {workspace?.last_build_at
                ? new Date(workspace.last_build_at).toLocaleString()
                : "—"}
            </span>
          </div>
        </div>
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button onClick={onDeploy} disabled={!canDeploy || pending} className="gap-2">
          <Rocket className="h-3.5 w-3.5" />
          {pending ? "Deploying…" : alreadyDeployed ? "Redeploy" : "Deploy"}
        </Button>
      </DialogFooter>
    </>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — Success
// ---------------------------------------------------------------------------

function SuccessStep({
  result,
  language,
  onLanguageChange,
  endpoint,
  base,
  selectedKeyId,
  onSelectedKeyChange,
  onClose,
}: {
  workspace: WorkspaceSummary | null;
  result: DeployResponse;
  language: Language;
  onLanguageChange: (l: Language) => void;
  endpoint: string;
  base: string;
  selectedKeyId: string | null;
  onSelectedKeyChange: (id: string | null) => void;
  onClose: () => void;
}) {
  const keysQuery = useQuery({
    queryKey: ["api-keys"],
    queryFn: api.listApiKeys,
    // TanStack default ``refetchOnWindowFocus=true`` picks up keys the user
    // minted in another tab after clicking "Create key".
  });

  // Keys that would actually work for this workspace: not revoked, and
  // either account-scoped (workspace_id=null) or pinned to this workspace.
  const eligibleKeys = React.useMemo(() => {
    const all = keysQuery.data ?? [];
    return all.filter(
      (k) =>
        !k.revoked_at &&
        (!k.workspace_id || k.workspace_id === result.workspace_id),
    );
  }, [keysQuery.data, result.workspace_id]);

  // Auto-select the first eligible key so the snippet shows a real prefix
  // out of the box. If there are none, fall through to the placeholder.
  React.useEffect(() => {
    if (selectedKeyId === null && eligibleKeys.length > 0) {
      onSelectedKeyChange(eligibleKeys[0].id);
    }
    if (
      selectedKeyId !== null &&
      !eligibleKeys.find((k) => k.id === selectedKeyId)
    ) {
      onSelectedKeyChange(eligibleKeys[0]?.id ?? null);
    }
  }, [eligibleKeys, selectedKeyId, onSelectedKeyChange]);

  const selectedKey = eligibleKeys.find((k) => k.id === selectedKeyId) ?? null;

  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          <CheckCircle2 className="h-5 w-5 text-emerald-500" />
          &ldquo;{result.workspace_name}&rdquo; is live
        </DialogTitle>
        <DialogDescription>
          {result.first_deploy
            ? "The graph is now queryable through the public API."
            : "Deployment refreshed — existing API keys keep working."}
        </DialogDescription>
      </DialogHeader>

      <DeploymentFacts
        endpoint={endpoint}
        workspaceId={result.workspace_id}
        latestJobId={result.latest_job_id}
        deployedAt={result.deployed_at}
      />

      <ApiKeySection
        eligibleKeys={eligibleKeys}
        selectedKey={selectedKey}
        onSelect={onSelectedKeyChange}
        loading={keysQuery.isLoading}
      />

      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs uppercase tracking-wider text-muted-foreground">
            Example call
          </p>
          <div className="flex items-center gap-1">
            {(["python", "typescript", "curl"] as Language[]).map((l) => (
              <button
                key={l}
                type="button"
                onClick={() => onLanguageChange(l)}
                className={cn(
                  "px-2 py-0.5 rounded-full text-[10px] border transition-colors",
                  language === l
                    ? "bg-foreground text-background border-foreground"
                    : "bg-card hover:bg-accent/60",
                )}
              >
                {l === "typescript" ? "TypeScript" : l === "python" ? "Python" : "curl"}
              </button>
            ))}
          </div>
        </div>
        <CodeSnippet
          lang={language === "curl" ? "curl" : language === "python" ? "python" : "typescript"}
          code={buildSnippet(
            language,
            base,
            selectedKey?.prefix ?? null,
            result.workspace_id,
            result.latest_job_id,
          )}
        />
        <p className="text-[11px] text-muted-foreground">
          {selectedKey ? (
            <>
              Replace <code className="font-mono">YOUR_KEY</code> with the full
              secret — it&apos;s shown only once at creation in{" "}
              <Link href="/api-keys" className="underline hover:text-foreground" target="_blank">
                /api-keys
              </Link>
              . The prefix <code className="font-mono">{selectedKey.prefix}</code>
              {" "}is just a hint so you know which key to use.
            </>
          ) : (
            <>
              You&apos;ll need an API key to run this.{" "}
              <Link href="/api-keys" className="underline hover:text-foreground" target="_blank">
                Create one now →
              </Link>
            </>
          )}
        </p>
      </div>

      <DialogFooter className="sm:justify-between">
        <Link
          href="/api-docs"
          target="_blank"
          className="text-xs text-muted-foreground underline hover:text-foreground self-center inline-flex items-center gap-1"
        >
          View full docs <ExternalLink className="h-3 w-3" />
        </Link>
        <Button onClick={onClose}>Done</Button>
      </DialogFooter>
    </>
  );
}

// ---------------------------------------------------------------------------
// Deployment facts — endpoint + ids + timestamp, each copyable
// ---------------------------------------------------------------------------

function DeploymentFacts({
  endpoint,
  workspaceId,
  latestJobId,
  deployedAt,
}: {
  endpoint: string;
  workspaceId: string;
  latestJobId: string;
  deployedAt: string;
}) {
  return (
    <div className="rounded-lg border bg-muted/30 divide-y">
      <FactRow label="Endpoint" value={endpoint} mono />
      <FactRow label="Graph ID" value={workspaceId} mono />
      <FactRow label="Build" value={latestJobId} mono />
      <FactRow
        label="Deployed"
        value={relativeTime(deployedAt)}
        absoluteValue={new Date(deployedAt).toLocaleString()}
      />
    </div>
  );
}

function FactRow({
  label,
  value,
  mono,
  absoluteValue,
}: {
  label: string;
  value: string;
  mono?: boolean;
  absoluteValue?: string;
}) {
  const [copied, setCopied] = React.useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      toast.error("Clipboard copy failed.");
    }
  };
  return (
    <div className="flex items-center justify-between gap-3 px-3 py-2 text-xs">
      <span className="text-muted-foreground shrink-0 w-[72px]">{label}</span>
      <span
        className={cn("flex-1 min-w-0 truncate", mono && "font-mono")}
        title={absoluteValue ?? value}
      >
        {value}
      </span>
      {mono && (
        <button
          type="button"
          onClick={onCopy}
          aria-label={`Copy ${label.toLowerCase()}`}
          className="shrink-0 h-6 w-6 rounded-md hover:bg-accent flex items-center justify-center transition-colors"
        >
          {copied ? (
            <Check className="h-3.5 w-3.5 text-emerald-500" />
          ) : (
            <Copy className="h-3.5 w-3.5 text-muted-foreground" />
          )}
        </button>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// API-key picker (no plaintext — selection drives the snippet's prefix hint)
// ---------------------------------------------------------------------------

function ApiKeySection({
  eligibleKeys,
  selectedKey,
  onSelect,
  loading,
}: {
  eligibleKeys: ApiKeyRow[];
  selectedKey: ApiKeyRow | null;
  onSelect: (id: string | null) => void;
  loading: boolean;
}) {
  const hasKeys = eligibleKeys.length > 0;

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
          <KeyRound className="h-3 w-3" /> API key
        </p>
        <Link
          href="/api-keys"
          target="_blank"
          className="text-[11px] text-muted-foreground hover:text-foreground inline-flex items-center gap-1 transition-colors"
        >
          Manage <ExternalLink className="h-3 w-3" />
        </Link>
      </div>

      {loading ? (
        <div className="h-9 rounded-md border bg-muted/40 animate-pulse" />
      ) : hasKeys ? (
        <div className="flex items-center gap-2">
          <select
            value={selectedKey?.id ?? ""}
            onChange={(e) => onSelect(e.target.value || null)}
            className="flex-1 h-9 rounded-md border bg-background px-3 text-sm font-mono"
          >
            {eligibleKeys.map((k) => {
              const scope = k.workspace_id ? "this graph" : "all graphs";
              const lastUsed = k.last_used_at
                ? ` · last used ${relativeTime(k.last_used_at)}`
                : " · never used";
              return (
                <option key={k.id} value={k.id}>
                  {k.name} — {k.prefix} ({scope}){lastUsed}
                </option>
              );
            })}
          </select>
        </div>
      ) : (
        <EmptyKeyState />
      )}

      {hasKeys && selectedKey && (
        // ``Badge`` renders as a ``<div>``; wrapping in ``<p>`` triggers the
        // Next.js "<div> cannot be a descendant of <p>" hydration error. Use
        // a ``<div>`` with the same typography.
        <div className="text-[11px] text-muted-foreground flex items-center gap-1 flex-wrap">
          <span>Using</span>
          <span className="font-mono">{selectedKey.prefix}</span>
          <Badge variant="outline" className="text-[9px] h-4 px-1 ml-0.5">
            {selectedKey.workspace_id ? "workspace" : "account"}-scoped
          </Badge>
          <span>·</span>
          <span>{selectedKey.rate_limit_rpm} rpm</span>
        </div>
      )}
    </div>
  );
}

function EmptyKeyState() {
  return (
    <div className="rounded-lg border border-dashed bg-card/40 px-3 py-3 flex items-start gap-2">
      <Zap className="h-4 w-4 text-muted-foreground shrink-0 mt-0.5" />
      <div className="flex-1 text-xs space-y-1.5">
        <p className="text-foreground">
          You don&apos;t have an API key yet.
        </p>
        <p className="text-muted-foreground">
          Create one to start calling this graph from your own code. Keys are
          shown once, so keep them in a password manager or secret store.
        </p>
        <Button
          asChild
          size="sm"
          variant="outline"
          className="gap-1.5 h-7 text-[11px]"
        >
          <Link href="/api-keys" target="_blank">
            <Plus className="h-3 w-3" />
            Create API key
            <ExternalLink className="h-3 w-3" />
          </Link>
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Snippet builder
// ---------------------------------------------------------------------------

function buildSnippet(
  lang: Language,
  base: string,
  keyPrefix: string | null,
  workspaceId: string,
  jobId: string,
): string {
  // Use a clearly-marked placeholder that the user MUST replace before running.
  // If we know the user's key prefix we surface it as a comment hint so they
  // know which key to paste; the secret itself stays at /api-keys.
  const keyHint = keyPrefix ? `${keyPrefix}YOUR_KEY` : "og_live_YOUR_KEY";
  const hintComment = keyPrefix
    ? `# Full key lives in /api-keys — prefix ${keyPrefix} is your selected one.`
    : "# Create one at /api-keys — the full secret is shown only once.";

  if (lang === "python") {
    return `# pip install opengraph-sdk
from opengraph_sdk import Client

${hintComment}
client = Client(
    api_key="${keyHint}",
    base_url="${base}",
)

# Graph ID ${workspaceId}  ·  Build ${jobId}
resp = client.query(
    workspace_id="${workspaceId}",
    query="What's in this knowledge base?",
)
print(resp.response)`;
  }
  if (lang === "typescript") {
    const tsComment = keyPrefix
      ? `// Full key lives in /api-keys — prefix ${keyPrefix} is your selected one.`
      : "// Create one at /api-keys — the full secret is shown only once.";
    return `// npm install @opengraph/sdk
import { OpenGraphClient } from "@opengraph/sdk";

${tsComment}
const client = new OpenGraphClient({
  apiKey: "${keyHint}",
  baseUrl: "${base}",
});

// Graph ID ${workspaceId}  ·  Build ${jobId}
const resp = await client.query({
  workspaceId: "${workspaceId}",
  query: "What's in this knowledge base?",
});
console.log(resp.response);`;
  }
  const curlComment = keyPrefix
    ? `# Full key lives in /api-keys — prefix ${keyPrefix} is your selected one.`
    : "# Create one at /api-keys — the full secret is shown only once.";
  return `${curlComment}
# Graph ID ${workspaceId}  ·  Build ${jobId}
curl -X POST ${base}/api/v1/ext/query \\
  -H "Authorization: Bearer ${keyHint}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "workspace_id": "${workspaceId}",
    "query": "What is in this knowledge base?"
  }'`;
}

// ---------------------------------------------------------------------------
// Misc helpers
// ---------------------------------------------------------------------------

function relativeTime(iso: string): string {
  const t = new Date(iso).getTime();
  const diff = Date.now() - t;
  if (diff < 60_000) return "just now";
  const mins = Math.floor(diff / 60_000);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

// Render a "Deployed" badge in the workspace card. Re-exported so the card
// can use it without hardcoding Tailwind classes inline.
export function DeployedBadge() {
  return (
    <Badge variant="secondary" className="gap-1 text-[10px] h-4 px-1.5">
      <CheckCircle2 className="h-3 w-3 text-emerald-500" />
      deployed
    </Badge>
  );
}
