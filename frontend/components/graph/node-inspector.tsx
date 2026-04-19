"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, FileText, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { api } from "@/lib/api";

interface Props {
  nodeId: string | null;
  onClose: () => void;
  onNavigate: (id: string) => void;
}

export function NodeInspector({ nodeId, onClose, onNavigate }: Props) {
  const query = useQuery({
    queryKey: ["node", nodeId],
    queryFn: () => api.getNode(nodeId!),
    enabled: Boolean(nodeId),
  });

  if (!nodeId) {
    return (
      <div className="h-full flex flex-col items-center justify-center text-center px-6 text-sm text-muted-foreground">
        <FileText className="h-6 w-6 mb-2 opacity-40" />
        Click any node on the canvas to inspect it.
      </div>
    );
  }

  if (query.isLoading) return <div className="p-6 text-sm text-muted-foreground">Loading node…</div>;
  if (query.isError) return <div className="p-6 text-sm text-destructive">Error: {(query.error as Error).message}</div>;
  const data = query.data;
  if (!data) return null;

  const n = data.node;
  const outgoing = data.edges.filter((e) => e.target && "target" in e);
  const incoming = data.edges.filter((e) => e.source && "source" in e);

  return (
    <div className="h-full flex flex-col">
      <div className="px-4 h-12 border-b flex items-center justify-between gap-2">
        <Badge variant="outline" className="text-[10px] font-mono">{String(n.node_type)}</Badge>
        <Button variant="ghost" size="icon" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>
      <ScrollArea className="flex-1">
        <div className="p-4 space-y-4">
          <div>
            <h3 className="font-semibold leading-snug">{String(n.heading)}</h3>
            <div className="flex flex-wrap gap-1.5 mt-2">
              <Badge variant="secondary" className="text-[10px]">{String(n.kb_source)}</Badge>
              <Badge variant="outline" className="text-[10px] font-mono break-all">{String(n.node_id)}</Badge>
            </div>
          </div>

          {n.content_summary && (
            <div className="space-y-1">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium">Summary</p>
              <p className="text-xs leading-relaxed">{String(n.content_summary)}</p>
            </div>
          )}

          {/* Tool-specific fields */}
          {Boolean(n.provider || n.purpose) && (
            <>
              <Separator />
              <div className="space-y-2">
                {n.provider ? <InfoRow label="Provider" value={String(n.provider)} /> : null}
                {n.category ? <InfoRow label="Category" value={String(n.category)} /> : null}
                {n.sla ? <InfoRow label="SLA" value={String(n.sla)} /> : null}
                {n.data_classification ? <InfoRow label="Data class" value={String(n.data_classification)} /> : null}
                {Array.isArray(n.connected_systems) && (n.connected_systems as unknown[]).length > 0 && (
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium mb-1">Integrates with</p>
                    <div className="flex flex-wrap gap-1">
                      {(n.connected_systems as string[]).map((s) => (
                        <Badge key={s} variant="outline" className="text-[10px]">{s}</Badge>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </>
          )}

          {/* Raw text */}
          {n.raw_text && typeof n.raw_text === "string" && n.raw_text.length > 0 && (
            <>
              <Separator />
              <details>
                <summary className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium cursor-pointer">
                  Raw text · {n.raw_text.length} chars
                </summary>
                <pre className="mt-2 p-2 rounded bg-muted/40 text-xs whitespace-pre-wrap break-words leading-relaxed">{n.raw_text}</pre>
              </details>
            </>
          )}

          <Separator />

          {outgoing.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium mb-1.5">Outgoing edges · {outgoing.length}</p>
              <EdgeList edges={outgoing} direction="out" onNavigate={onNavigate} />
            </div>
          )}
          {incoming.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium mb-1.5">Incoming edges · {incoming.length}</p>
              <EdgeList edges={incoming} direction="in" onNavigate={onNavigate} />
            </div>
          )}

          {data.children.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium mb-1.5">Children · {data.children.length}</p>
              <div className="flex flex-col gap-1">
                {data.children.slice(0, 30).map((cid) => (
                  <button key={cid} className="text-left text-xs hover:text-primary flex items-center gap-1 font-mono truncate" onClick={() => onNavigate(cid)}>
                    <ChevronRight className="h-3 w-3 shrink-0" />
                    {cid}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
    </div>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2 text-xs">
      <span className="text-muted-foreground min-w-[80px]">{label}</span>
      <span className="flex-1">{value}</span>
    </div>
  );
}

function EdgeList({
  edges,
  direction,
  onNavigate,
}: {
  edges: Array<{ target?: string; source?: string; edge_type: string; weight: number; [k: string]: unknown }>;
  direction: "out" | "in";
  onNavigate: (id: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      {edges.slice(0, 30).map((e, i) => {
        const nid = direction === "out" ? e.target : e.source;
        if (!nid) return null;
        return (
          <button
            key={`${nid}-${i}`}
            onClick={() => onNavigate(nid)}
            className="flex items-center gap-2 text-left hover:text-primary text-xs group"
          >
            <Badge variant="outline" className="text-[9px] font-mono shrink-0">{e.edge_type}</Badge>
            <span className="font-mono truncate group-hover:underline">{nid}</span>
            <span className="ml-auto text-[10px] text-muted-foreground">w={e.weight.toFixed(2)}</span>
          </button>
        );
      })}
    </div>
  );
}
