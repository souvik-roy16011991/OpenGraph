"use client";

import { NODE_TYPE_COLORS, EDGE_TYPE_COLORS, EDGE_TYPE_STYLES } from "@/lib/cytoscape-style";

export function Legend({ className }: { className?: string }) {
  return (
    <div className={className}>
      <div className="rounded-md border bg-card/90 backdrop-blur p-3 shadow-sm space-y-2">
        <div>
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium mb-1.5">Nodes</p>
          <div className="grid grid-cols-2 gap-x-3 gap-y-1">
            {Object.entries(NODE_TYPE_COLORS).map(([type, c]) => (
              <div key={type} className="flex items-center gap-1.5 text-[11px]">
                <span className="h-2.5 w-2.5 rounded-full border" style={{ background: c.bg, borderColor: c.border }} />
                {type}
              </div>
            ))}
          </div>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium mb-1.5">Edges</p>
          <div className="flex flex-col gap-1">
            {Object.entries(EDGE_TYPE_COLORS).slice(0, 6).map(([type, color]) => {
              const s = EDGE_TYPE_STYLES[type];
              return (
                <div key={type} className="flex items-center gap-2 text-[11px]">
                  <span className="inline-block w-6 h-[2px]" style={{ background: color, borderTop: s?.line === "dashed" ? `2px dashed ${color}` : s?.line === "dotted" ? `2px dotted ${color}` : undefined, height: s?.line !== "solid" ? 0 : 2 }} />
                  <span className="font-mono">{type}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
