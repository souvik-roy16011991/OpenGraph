"use client";

import * as React from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Send, Sparkles, User, Bot, Loader2,
  CheckCircle2, ChevronDown, ChevronRight, AlertCircle,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { errorMessage } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ModelSelect } from "@/components/ui/model-select";
import { api } from "@/lib/api";
import type { LLMModel, QueryResponse } from "@/lib/schema";
import { useWizardStore } from "@/store/wizard-store";
import { useWorkspaceStore } from "@/store/workspace-store";

type ProgressEntry = { stage: string; label: string; preview?: string; ts: number };

type AssistantTurn = {
  role: "assistant";
  id: number;
  status: "running" | "done" | "error";
  progress: ProgressEntry[];
  startedAt: number;
  finishedAt?: number;
  resp?: QueryResponse;
  errorMsg?: string;
};

type Turn =
  | { role: "user"; text: string; id: number }
  | AssistantTurn;

interface ChatProps {
  /** When provided, the chat runs against *this* workspace regardless of
   *  the sidebar's active workspace. The /chat Playground page sets this
   *  from the chat-store; the legacy wizard /query page leaves it undefined
   *  so the global store wins (backwards-compat). */
  workspaceId?: string | null;
}

export function Chat({ workspaceId }: ChatProps = {}) {
  const [turns, setTurns] = React.useState<Turn[]>([]);
  const [input, setInput] = React.useState("");
  const [sessionId, setSessionId] = React.useState<string | null>(null);
  const [llmModel, setLlmModel] = React.useState<string | null>(null);
  const setHighlighted = useWizardStore((s) => s.setHighlightedNodeIds);
  const globalActive = useWorkspaceStore((s) => s.activeId);
  // Chat's effective workspace: prop wins, fall back to global sidebar pick.
  const activeWs = workspaceId ?? globalActive;
  const scrollerRef = React.useRef<HTMLDivElement>(null);

  // Catalog fetch — cached 1h on the backend (Upstash), 10 min on client.
  const modelsQuery = useQuery({
    queryKey: ["llm", "models"],
    queryFn: () => api.listModels(),
    staleTime: 10 * 60 * 1000,
  });

  // Workspace's current LLM preference (re-fetched on workspace switch).
  const wsLlmQuery = useQuery({
    queryKey: ["workspace", activeWs, "llm"],
    queryFn: () => (activeWs ? api.getWorkspaceLLM(activeWs) : Promise.resolve(null)),
    enabled: !!activeWs,
    staleTime: 30 * 1000,
  });

  React.useEffect(() => {
    if (wsLlmQuery.data) setLlmModel(wsLlmQuery.data.llm_model);
  }, [wsLlmQuery.data]);

  // Persist model choice to the workspace. Fire-and-forget; the UI already
  // reflects the new model locally so the PUT just makes it sticky.
  const saveLlm = useMutation({
    mutationFn: (model: string) =>
      activeWs ? api.setWorkspaceLLM(activeWs, model) : Promise.reject(new Error("no workspace")),
  });

  function handleModelChange(id: string) {
    setLlmModel(id);
    saveLlm.mutate(id);
  }

  // Restore or create a session id per workspace (so switching workspaces
  // gives a fresh conversation thread rather than leaking history across).
  React.useEffect(() => {
    if (!activeWs) return;
    setTurns([]);
    const key = `kb.chatSessionId.${activeWs}`;
    try {
      const stored = localStorage.getItem(key);
      if (stored) { setSessionId(stored); return; }
      const fresh = crypto.randomUUID();
      localStorage.setItem(key, fresh);
      setSessionId(fresh);
    } catch {
      setSessionId(crypto.randomUUID());
    }
  }, [activeWs]);

  // True while any assistant turn is still streaming. Drives the
  // composer-disabled state and gates re-submits.
  const isRunning = turns.some(
    (t) => t.role === "assistant" && t.status === "running",
  );

  // AbortController for the in-flight stream — `submit` aborts the
  // previous stream before starting a new one and the unmount cleanup
  // aborts whatever's in flight when the user navigates away.
  const abortRef = React.useRef<AbortController | null>(null);
  React.useEffect(() => () => abortRef.current?.abort(), []);

  React.useEffect(() => {
    const v = scrollerRef.current?.querySelector<HTMLElement>("[data-radix-scroll-area-viewport]");
    if (v) v.scrollTop = v.scrollHeight;
  }, [turns, isRunning]);

  async function submit(text: string) {
    const q = text.trim();
    if (!q || isRunning) return;

    const userId = Date.now();
    const assistantId = userId + 1;
    const startedAt = Date.now();
    setTurns((t) => [
      ...t,
      { role: "user", text: q, id: userId },
      {
        role: "assistant",
        id: assistantId,
        status: "running",
        progress: [],
        startedAt,
      },
    ]);
    setInput("");

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const patchAssistant = (
      updater: (a: AssistantTurn) => AssistantTurn,
    ) => {
      setTurns((t) =>
        t.map((entry) =>
          entry.role === "assistant" && entry.id === assistantId
            ? updater(entry)
            : entry,
        ),
      );
    };

    try {
      await api.streamQuery(
        {
          query: q,
          session_id: sessionId ?? undefined,
          llm_model: llmModel ?? undefined,
        },
        // When the parent passed a workspaceId prop, use it as the
        // X-Workspace-Id override; otherwise the API helper reads the
        // sidebar-store activeId via withHeaders.
        workspaceId ?? undefined,
        (evt) => {
          // Discriminate by property presence — TS can't narrow `evt.stage`
          // alone because the progress variant has an open `stage: string`.
          if ("result" in evt) {
            const resp = evt.result;
            patchAssistant((a) => ({
              ...a,
              status: "done",
              finishedAt: Date.now(),
              resp,
            }));
            setHighlighted(resp.traversal_path || []);
            if (resp.session_id && resp.session_id !== sessionId) {
              setSessionId(resp.session_id);
              if (activeWs) {
                try { localStorage.setItem(`kb.chatSessionId.${activeWs}`, resp.session_id); } catch {}
              }
            }
          } else if ("error" in evt) {
            patchAssistant((a) => ({
              ...a,
              status: "error",
              finishedAt: Date.now(),
              errorMsg: evt.error,
            }));
          } else {
            patchAssistant((a) => ({
              ...a,
              progress: [
                ...a.progress,
                {
                  stage: evt.stage,
                  label: evt.label,
                  preview: evt.preview,
                  ts: Date.now(),
                },
              ],
            }));
          }
        },
        controller.signal,
      );
    } catch (err) {
      if (controller.signal.aborted) return;
      patchAssistant((a) => ({
        ...a,
        status: "error",
        finishedAt: Date.now(),
        errorMsg: err instanceof Error ? err.message : String(err),
      }));
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-180px)] rounded-xl border bg-card">
      <div className="flex items-center justify-between border-b px-3 h-11">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Bot className="h-3.5 w-3.5" />
          <span>Model</span>
        </div>
        <div className="flex items-center gap-2">
          {saveLlm.isPending && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
          {saveLlm.isError && (
            <span className="text-[10px] text-destructive" title={errorMessage(saveLlm.error)}>
              save failed
            </span>
          )}
          <ModelSelect
            models={modelsQuery.data?.models ?? []}
            value={llmModel}
            onChange={handleModelChange}
            defaultModel={modelsQuery.data?.default}
            loading={modelsQuery.isLoading}
            disabled={!activeWs}
          />
        </div>
      </div>
      <ScrollArea ref={scrollerRef} className="flex-1">
        <div className="p-6 space-y-5">
          {turns.length === 0 && (
            <div className="text-center py-16">
              <Sparkles className="mx-auto h-8 w-8 text-muted-foreground mb-3" />
              <h3 className="font-semibold">Ask about anything in the graph</h3>
              <p className="text-sm text-muted-foreground mt-1">Try questions like process steps, tool capabilities, or definitions.</p>
              <div className="mt-4 flex flex-wrap gap-2 justify-center">
                {["How is FOIR calculated?", "What tools check credit scores?", "Walk me through the KYC process"].map((s) => (
                  <Button key={s} variant="outline" size="sm" onClick={() => submit(s)}>{s}</Button>
                ))}
              </div>
            </div>
          )}

          {turns.map((t) =>
            t.role === "user" ? (
              <div key={t.id} className="flex gap-3 justify-end">
                <div className="max-w-[75%] rounded-2xl rounded-tr-sm bg-primary text-primary-foreground px-4 py-2.5 text-sm">
                  {t.text}
                </div>
                <div className="h-8 w-8 rounded-full bg-primary/20 flex items-center justify-center shrink-0">
                  <User className="h-4 w-4" />
                </div>
              </div>
            ) : (
              <div key={t.id} className="flex gap-3">
                <div className="h-8 w-8 rounded-full bg-gradient-to-br from-indigo-500 to-emerald-500 flex items-center justify-center shrink-0">
                  <Bot className="h-4 w-4 text-white" />
                </div>
                <div className="flex-1 min-w-0 space-y-3 max-w-[85%]">
                  {(t.status === "running" || t.progress.length > 0) && (
                    <ThinkingTimeline turn={t} />
                  )}
                  {t.status === "done" && t.resp && (
                    <AgentMessage
                      resp={t.resp}
                      onFollowUp={submit}
                      modelLabel={labelForModel(t.resp.llm_model, modelsQuery.data?.models)}
                    />
                  )}
                  {t.status === "error" && (
                    <div className="rounded-2xl rounded-tl-sm border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm flex items-start gap-2">
                      <AlertCircle className="h-4 w-4 text-destructive mt-0.5 shrink-0" />
                      <div className="min-w-0">
                        <div className="font-medium text-destructive">Error</div>
                        <div className="text-muted-foreground mt-0.5 break-words">
                          {t.errorMsg ?? "Something went wrong."}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )
          )}
        </div>
      </ScrollArea>
      <form
        className="border-t p-3 flex gap-2"
        onSubmit={(e) => { e.preventDefault(); submit(input); }}
      >
        <Input
          autoFocus
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter" && input.trim() && !isRunning) {
              e.preventDefault();
              submit(input);
            }
          }}
          placeholder="Ask a question about the graph… (⌘↵ to send)"
          className="flex-1"
          disabled={isRunning}
        />
        <Button type="submit" disabled={isRunning || !input.trim()}>
          <Send className="h-4 w-4" /> Send
        </Button>
      </form>
    </div>
  );
}

/** Live "thinking" timeline rendered inside an assistant turn. While
 *  the stream is running, the most recent step shows a spinner and the
 *  elapsed clock ticks. Once `done`, the whole panel auto-collapses to
 *  a single "Thought for Xs · N steps" line; click to re-expand. */
function ThinkingTimeline({ turn }: { turn: AssistantTurn }) {
  const [open, setOpen] = React.useState(true);
  const isRunning = turn.status === "running";
  const isError = turn.status === "error";

  // Auto-collapse when the run finishes successfully. Keep open on
  // error so the user sees where the pipeline stalled.
  React.useEffect(() => {
    if (turn.status === "done") setOpen(false);
  }, [turn.status]);

  // Tick every 300ms while running so the elapsed clock updates.
  const [, force] = React.useReducer((x: number) => x + 1, 0);
  React.useEffect(() => {
    if (!isRunning) return;
    const i = setInterval(force, 300);
    return () => clearInterval(i);
  }, [isRunning]);

  const elapsedMs = (turn.finishedAt ?? Date.now()) - turn.startedAt;
  const elapsedSec = (elapsedMs / 1000).toFixed(1);
  const stepCount = turn.progress.length;
  const stepLabel = `${stepCount} step${stepCount === 1 ? "" : "s"}`;

  return (
    <div className="rounded-xl border bg-muted/30 text-sm overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-muted/50 transition-colors"
      >
        {isRunning ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-500 shrink-0" />
        ) : isError ? (
          <AlertCircle className="h-3.5 w-3.5 text-destructive shrink-0" />
        ) : (
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
        )}
        <span className="font-medium text-xs">
          {isRunning
            ? `Thinking… ${elapsedSec}s · ${stepLabel}`
            : `Thought for ${elapsedSec}s · ${stepLabel}`}
        </span>
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 ml-auto text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 ml-auto text-muted-foreground" />
        )}
      </button>
      {open && (
        <ol className="px-3 pb-3 pt-2 space-y-1.5 border-t bg-background/40">
          {turn.progress.map((p, i) => {
            const isLast = i === turn.progress.length - 1 && isRunning;
            return (
              <li key={`${p.stage}-${i}`} className="flex items-start gap-2 text-xs">
                <span className="mt-0.5 shrink-0">
                  {isLast ? (
                    <Loader2 className="h-3 w-3 animate-spin text-indigo-500" />
                  ) : (
                    <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                  )}
                </span>
                <span className="flex-1 min-w-0">
                  <span className="font-medium">{p.label}</span>
                  {p.preview && (
                    <span className="text-muted-foreground"> · {p.preview}</span>
                  )}
                </span>
              </li>
            );
          })}
          {isRunning && turn.progress.length === 0 && (
            <li className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              <span>Starting…</span>
            </li>
          )}
        </ol>
      )}
    </div>
  );
}

/** Prefer the model's friendly name from the catalog; fall back to the raw id. */
function labelForModel(id: string | null | undefined, catalog: LLMModel[] | undefined): string | null {
  if (!id) return null;
  const hit = catalog?.find((m) => m.id === id);
  return hit?.name || id;
}

function AgentMessage({
  resp,
  onFollowUp,
  modelLabel,
}: {
  resp: QueryResponse;
  onFollowUp: (q: string) => void;
  modelLabel: string | null;
}) {
  return (
    <div className="space-y-3">
      <div className="rounded-2xl rounded-tl-sm bg-muted/60 px-4 py-3 text-sm prose prose-sm dark:prose-invert max-w-none">
        <ReactMarkdown>{resp.response || "_(empty response)_"}</ReactMarkdown>
      </div>

      {(resp.intent || resp.kb_focus || modelLabel) && (
        <div className="flex flex-wrap gap-1.5 text-[10px]">
          {modelLabel && (
            <Badge
              variant="outline"
              className="text-[10px] font-mono max-w-[260px] truncate"
              title={resp.llm_model ?? undefined}
            >
              model: {modelLabel}
            </Badge>
          )}
          {resp.intent && <Badge variant="outline" className="text-[10px] font-mono">intent: {resp.intent}</Badge>}
          {resp.kb_focus && <Badge variant="outline" className="text-[10px] font-mono">focus: {resp.kb_focus}</Badge>}
          {resp.extracted_topics.map((t) => (
            <Badge key={t} variant="secondary" className="text-[10px]">{t}</Badge>
          ))}
        </div>
      )}

      {resp.tools_referenced.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium mb-1.5">Tools referenced</p>
          <div className="grid gap-1.5 sm:grid-cols-2">
            {resp.tools_referenced.slice(0, 6).map((t, i) => (
              <Card key={i} className="p-2.5">
                <p className="text-xs font-medium truncate">{String((t as Record<string, unknown>).tool_name ?? (t as Record<string, unknown>).heading ?? "Tool")}</p>
                {(t as Record<string, unknown>).provider ? <p className="text-[10px] text-muted-foreground truncate">{String((t as Record<string, unknown>).provider)}</p> : null}
                {(t as Record<string, unknown>).purpose ? <p className="text-[11px] leading-snug mt-1 line-clamp-2">{String((t as Record<string, unknown>).purpose)}</p> : null}
              </Card>
            ))}
          </div>
        </div>
      )}

      {resp.knowledge_concepts.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium mb-1.5">Knowledge concepts</p>
          <div className="flex flex-wrap gap-1.5">
            {resp.knowledge_concepts.slice(0, 12).map((k, i) => (
              <Badge key={i} variant="outline" className="text-[10px] max-w-[300px] truncate">
                {String((k as Record<string, unknown>).heading ?? "Concept")}
              </Badge>
            ))}
          </div>
        </div>
      )}

      {resp.traversal_path.length > 0 && (
        <p className="text-[10px] text-muted-foreground">
          Traversal path: <span className="font-mono">{resp.traversal_path.length} nodes</span> · highlighted in the graph explorer
        </p>
      )}

      {resp.follow_up_suggestions.length > 0 && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium mb-1.5">Follow-up suggestions</p>
          <div className="flex flex-wrap gap-1.5">
            {resp.follow_up_suggestions.map((s) => (
              <Button key={s} variant="outline" size="sm" className="h-7 text-xs" onClick={() => onFollowUp(s)}>
                {s}
              </Button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
