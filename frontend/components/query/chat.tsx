"use client";

import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { Send, Sparkles, User, Bot, Loader2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { api } from "@/lib/api";
import type { QueryResponse } from "@/lib/schema";
import { useWizardStore } from "@/store/wizard-store";
import { useWorkspaceStore } from "@/store/workspace-store";

type Turn =
  | { role: "user"; text: string; id: number }
  | { role: "assistant"; resp: QueryResponse; id: number };

export function Chat() {
  const [turns, setTurns] = React.useState<Turn[]>([]);
  const [input, setInput] = React.useState("");
  const [sessionId, setSessionId] = React.useState<string | null>(null);
  const setHighlighted = useWizardStore((s) => s.setHighlightedNodeIds);
  const activeWs = useWorkspaceStore((s) => s.activeId);
  const scrollerRef = React.useRef<HTMLDivElement>(null);

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

  const mutation = useMutation({
    mutationFn: (q: string) => api.query({ query: q, session_id: sessionId ?? undefined }),
    onSuccess: (resp) => {
      setTurns((t) => [...t, { role: "assistant", resp, id: Date.now() }]);
      setHighlighted(resp.traversal_path || []);
      if (resp.session_id && resp.session_id !== sessionId) {
        setSessionId(resp.session_id);
        if (activeWs) {
          try { localStorage.setItem(`kb.chatSessionId.${activeWs}`, resp.session_id); } catch {}
        }
      }
    },
    onError: (err: Error) => {
      setTurns((t) => [...t, { role: "assistant", resp: {
        query: "", intent: "error", kb_focus: "", extracted_topics: [],
        response: `**Error**: ${err.message}`, steps: [], tools_referenced: [],
        knowledge_concepts: [], follow_up_suggestions: [], traversal_path: [], error: err.message,
      }, id: Date.now() }]);
    },
  });

  React.useEffect(() => {
    const v = scrollerRef.current?.querySelector<HTMLElement>("[data-radix-scroll-area-viewport]");
    if (v) v.scrollTop = v.scrollHeight;
  }, [turns, mutation.isPending]);

  function submit(text: string) {
    const q = text.trim();
    if (!q) return;
    setTurns((t) => [...t, { role: "user", text: q, id: Date.now() }]);
    setInput("");
    mutation.mutate(q);
  }

  return (
    <div className="flex flex-col h-[calc(100vh-180px)] rounded-xl border bg-card">
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
                  <AgentMessage resp={t.resp} onFollowUp={submit} />
                </div>
              </div>
            )
          )}

          {mutation.isPending && (
            <div className="flex gap-3">
              <div className="h-8 w-8 rounded-full bg-gradient-to-br from-indigo-500 to-emerald-500 flex items-center justify-center shrink-0">
                <Bot className="h-4 w-4 text-white" />
              </div>
              <div className="flex items-center gap-2 text-sm text-muted-foreground pt-1.5">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Thinking…
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
      <form
        className="border-t p-3 flex gap-2"
        onSubmit={(e) => { e.preventDefault(); submit(input); }}
      >
        <Input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Ask a question about the graph…"
          className="flex-1"
          disabled={mutation.isPending}
        />
        <Button type="submit" disabled={mutation.isPending || !input.trim()}>
          <Send className="h-4 w-4" /> Send
        </Button>
      </form>
    </div>
  );
}

function AgentMessage({ resp, onFollowUp }: { resp: QueryResponse; onFollowUp: (q: string) => void }) {
  return (
    <div className="space-y-3">
      <div className="rounded-2xl rounded-tl-sm bg-muted/60 px-4 py-3 text-sm prose prose-sm dark:prose-invert max-w-none">
        <ReactMarkdown>{resp.response || "_(empty response)_"}</ReactMarkdown>
      </div>

      {(resp.intent || resp.kb_focus) && (
        <div className="flex flex-wrap gap-1.5 text-[10px]">
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
