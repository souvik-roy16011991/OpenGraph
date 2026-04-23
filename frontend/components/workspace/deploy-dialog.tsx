"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  CheckCircle2,
  Copy,
  Eye,
  EyeOff,
  Rocket,
  AlertCircle,
  Check,
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
import type { DeployResponse, WorkspaceSummary } from "@/lib/schema";
import { cn } from "@/lib/utils";

type Language = "python" | "typescript" | "curl";

type Props = {
  workspace: WorkspaceSummary | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

/**
 * Two-step deploy flow.
 *
 *   Step 1 — Configure: confirm language, see graph stats, click Deploy.
 *   Step 2 — Success:   copy the plaintext API key (shown once) + code
 *                       snippet pre-filled with the workspace + build id.
 *
 * When the user closes the success step we reset internal state so the
 * next click on Deploy opens clean — plaintext MUST never be retained in
 * state across opens.
 */
export function DeployDialog({ workspace, open, onOpenChange }: Props) {
  const qc = useQueryClient();
  const [language, setLanguage] = React.useState<Language>("python");
  const [result, setResult] = React.useState<DeployResponse | null>(null);
  const [revealed, setRevealed] = React.useState(false);
  const [copiedField, setCopiedField] = React.useState<string | null>(null);

  const base = typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.hostname}:8000`
    : "https://api.opengraph.example";

  const reset = React.useCallback(() => {
    setResult(null);
    setRevealed(false);
    setCopiedField(null);
  }, []);

  const handleClose = (v: boolean) => {
    onOpenChange(v);
    if (!v) setTimeout(reset, 250);
  };

  const deployMut = useMutation({
    mutationFn: (ws_id: string) => api.deployWorkspace(ws_id),
    onSuccess: (data) => {
      setResult(data);
      setRevealed(true);
      qc.invalidateQueries({ queryKey: ["workspaces"] });
      qc.invalidateQueries({ queryKey: ["api-keys"] });
      toast.success(
        data.first_deploy ? "Deployed — API is live" : "Redeployed — new key ready",
      );
    },
    onError: (err: Error) => toast.error(err.message),
  });

  async function copy(value: string, field: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopiedField(field);
      setTimeout(() => setCopiedField(null), 1800);
    } catch {
      toast.error("Couldn't copy — select the text manually.");
    }
  }

  const canDeploy = workspace?.last_build_status === "done";
  const stats = workspace?.stats ?? {};
  const nodeCount = (stats as { total_nodes?: number }).total_nodes ?? 0;
  const edgeCount = (stats as { total_edges?: number }).total_edges ?? 0;

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-lg">
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
            base={base}
            revealed={revealed}
            setRevealed={setRevealed}
            copiedField={copiedField}
            onCopy={copy}
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
          {alreadyDeployed ? (
            <>
              A new workspace-scoped API key will be minted. Existing keys
              for this graph remain valid until you revoke them from{" "}
              <Link href="/api-keys" className="underline hover:text-foreground">
                /api-keys
              </Link>
              .
            </>
          ) : (
            <>
              Make this graph queryable from your own code via a
              workspace-scoped API key. You&apos;ll copy the key once on the
              next screen.
            </>
          )}
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
          <div className="flex justify-between">
            <span className="text-muted-foreground">Rate limit</span>
            <span className="font-mono">60 requests / minute</span>
          </div>
        </div>
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onCancel} disabled={pending}>
          Cancel
        </Button>
        <Button
          onClick={onDeploy}
          disabled={!canDeploy || pending}
          className="gap-2"
        >
          <Rocket className="h-3.5 w-3.5" />
          {pending ? "Deploying…" : alreadyDeployed ? "Redeploy & mint new key" : "Deploy"}
        </Button>
      </DialogFooter>
    </>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — Success (plaintext key + snippet)
// ---------------------------------------------------------------------------

function SuccessStep({
  workspace,
  result,
  language,
  onLanguageChange,
  base,
  revealed,
  setRevealed,
  copiedField,
  onCopy,
  onClose,
}: {
  workspace: WorkspaceSummary | null;
  result: DeployResponse;
  language: Language;
  onLanguageChange: (l: Language) => void;
  base: string;
  revealed: boolean;
  setRevealed: (v: boolean) => void;
  copiedField: string | null;
  onCopy: (value: string, field: string) => void;
  onClose: () => void;
}) {
  return (
    <>
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2">
          <CheckCircle2 className="h-5 w-5 text-emerald-500" />
          {result.first_deploy ? "Deployed" : "Redeployed"}
        </DialogTitle>
        <DialogDescription>
          <span className="font-medium text-foreground">
            &ldquo;{result.workspace_name}&rdquo;
          </span>{" "}
          is now reachable at{" "}
          <code className="text-[11px]">/api/v1/ext/query</code>. Copy the key
          below — the plaintext won&apos;t be shown again.
        </DialogDescription>
      </DialogHeader>

      <div className="space-y-3">
        <KeyRow
          label="API key"
          value={result.api_key_plaintext}
          revealable
          revealed={revealed}
          onToggleReveal={() => setRevealed(!revealed)}
          copied={copiedField === "key"}
          onCopy={() => onCopy(result.api_key_plaintext, "key")}
        />
        <KeyRow
          label="Graph ID"
          value={result.workspace_id}
          copied={copiedField === "ws"}
          onCopy={() => onCopy(result.workspace_id, "ws")}
        />
        <KeyRow
          label="Build ID"
          value={result.latest_job_id}
          copied={copiedField === "job"}
          onCopy={() => onCopy(result.latest_job_id, "job")}
        />
      </div>

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
            result.api_key_plaintext,
            result.workspace_id,
            result.latest_job_id,
          )}
        />
      </div>

      <DialogFooter className="sm:justify-between">
        <Link
          href="/api-keys"
          className="text-xs text-muted-foreground underline hover:text-foreground self-center"
        >
          Manage keys →
        </Link>
        <Button onClick={onClose}>Done</Button>
      </DialogFooter>
    </>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function KeyRow({
  label,
  value,
  revealable,
  revealed,
  onToggleReveal,
  copied,
  onCopy,
}: {
  label: string;
  value: string;
  revealable?: boolean;
  revealed?: boolean;
  onToggleReveal?: () => void;
  copied: boolean;
  onCopy: () => void;
}) {
  const display = revealable && !revealed ? value : value;
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-1">
        {label}
      </p>
      <div className="flex items-stretch gap-2">
        <div
          className={cn(
            "flex-1 rounded-md border bg-muted/40 px-3 py-2 font-mono text-xs break-all",
            revealable && !revealed && "select-none blur-[4px]",
          )}
        >
          {display}
        </div>
        {revealable && (
          <Button
            variant="outline"
            size="icon"
            type="button"
            onClick={onToggleReveal}
            aria-label={revealed ? "Hide" : "Reveal"}
            title={revealed ? "Hide" : "Reveal"}
          >
            {revealed ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </Button>
        )}
        <Button
          variant="outline"
          size="icon"
          type="button"
          onClick={onCopy}
          aria-label="Copy"
          title="Copy"
        >
          {copied ? (
            <Check className="h-4 w-4 text-emerald-500" />
          ) : (
            <Copy className="h-4 w-4" />
          )}
        </Button>
      </div>
    </div>
  );
}

function buildSnippet(
  lang: Language,
  base: string,
  key: string,
  workspaceId: string,
  jobId: string,
): string {
  if (lang === "python") {
    return `# pip install opengraph-sdk
from opengraph_sdk import Client

client = Client(
    api_key="${key}",
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
    return `// npm install @opengraph/sdk
import { OpenGraphClient } from "@opengraph/sdk";

const client = new OpenGraphClient({
  apiKey: "${key}",
  baseUrl: "${base}",
});

// Graph ID ${workspaceId}  ·  Build ${jobId}
const resp = await client.query({
  workspaceId: "${workspaceId}",
  query: "What's in this knowledge base?",
});
console.log(resp.response);`;
  }
  // curl
  return `# Graph ID ${workspaceId}  ·  Build ${jobId}
curl -X POST ${base}/api/v1/ext/query \\
  -H "Authorization: Bearer ${key}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "workspace_id": "${workspaceId}",
    "query": "What is in this knowledge base?"
  }'`;
}

// Render a "Deployed" badge in the workspace card. Exported so the card
// can use it without hardcoding Tailwind classes inline.
export function DeployedBadge() {
  return (
    <Badge variant="secondary" className="gap-1 text-[10px] h-4 px-1.5">
      <CheckCircle2 className="h-3 w-3 text-emerald-500" />
      deployed
    </Badge>
  );
}
