"use client";

import * as React from "react";
import { Filter, Search } from "lucide-react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const NODE_TYPES = ["domain", "chapter", "section", "table", "tool", "process", "glossary", "concept"];
const EDGE_TYPES = ["CONTAINS", "HAS_CONTENT", "NEXT_STEP", "INTEGRATES_WITH", "IMPLEMENTS", "USES_TOOL", "RELATED_TO"];

interface Props {
  nodeTypes: Set<string>;
  onNodeTypesChange: (s: Set<string>) => void;
  edgeTypes: Set<string>;
  onEdgeTypesChange: (s: Set<string>) => void;
  onSearch: (q: string) => void;
}

export function FilterBar({ nodeTypes, onNodeTypesChange, edgeTypes, onEdgeTypesChange, onSearch }: Props) {
  const [q, setQ] = React.useState("");

  function toggle(set: Set<string>, key: string, update: (s: Set<string>) => void) {
    const next = new Set(set);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    update(next);
  }

  return (
    <div className="border-b bg-background/80 backdrop-blur px-3 py-2 flex flex-wrap items-center gap-2">
      <form
        onSubmit={(e) => { e.preventDefault(); onSearch(q); }}
        className="flex items-center gap-2 mr-3"
      >
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search nodes…" className="h-8 pl-7 w-[220px]" />
        </div>
        <Button type="submit" size="sm" variant="secondary">Find</Button>
        {q && (
          <Button type="button" size="sm" variant="ghost" onClick={() => { setQ(""); onSearch(""); }}>Clear</Button>
        )}
      </form>

      <div className="flex items-center gap-1 flex-wrap">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground mr-1 flex items-center gap-1">
          <Filter className="h-3 w-3" /> Nodes
        </span>
        {NODE_TYPES.map((t) => {
          const active = nodeTypes.size === 0 || nodeTypes.has(t);
          return (
            <Badge
              key={t}
              variant={active ? "secondary" : "outline"}
              onClick={() => toggle(nodeTypes, t, onNodeTypesChange)}
              className={cn("cursor-pointer text-[10px] font-mono", !active && "opacity-50")}
            >
              {t}
            </Badge>
          );
        })}
      </div>

      <div className="flex items-center gap-1 flex-wrap">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground mr-1">Edges</span>
        {EDGE_TYPES.map((t) => {
          const active = edgeTypes.size === 0 || edgeTypes.has(t);
          return (
            <Badge
              key={t}
              variant={active ? "secondary" : "outline"}
              onClick={() => toggle(edgeTypes, t, onEdgeTypesChange)}
              className={cn("cursor-pointer text-[10px] font-mono", !active && "opacity-50")}
            >
              {t}
            </Badge>
          );
        })}
      </div>
    </div>
  );
}
