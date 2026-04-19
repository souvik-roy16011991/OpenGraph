"use client";

import * as React from "react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import { cytoscapeStylesheet, FCOSE_LAYOUT } from "@/lib/cytoscape-style";
import type { GraphVizPayload } from "@/lib/schema";

if (typeof window !== "undefined") {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  if (!(cytoscape as any).__fcoseRegistered) {
    cytoscape.use(fcose);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (cytoscape as any).__fcoseRegistered = true;
  }
}

export interface CytoscapeViewProps {
  payload: GraphVizPayload;
  onNodeClick?: (id: string) => void;
  highlightedIds?: string[];
  nodeTypeFilter?: Set<string>;
  edgeTypeFilter?: Set<string>;
}

function toElements(payload: GraphVizPayload, nodeFilter?: Set<string>, edgeFilter?: Set<string>): ElementDefinition[] {
  const nodes = payload.nodes
    .filter((n) => !nodeFilter || nodeFilter.size === 0 || nodeFilter.has(n.type))
    .map<ElementDefinition>((n) => ({
      data: {
        id: n.id,
        type: n.type,
        kb_source: n.kb_source,
        label: n.heading.length > 36 ? n.heading.slice(0, 34) + "…" : n.heading,
        heading: n.heading,
      },
    }));
  const allowedIds = new Set(nodes.map((n) => String(n.data.id)));
  const edges = payload.edges
    .filter((e) => allowedIds.has(e.source) && allowedIds.has(e.target))
    .filter((e) => !edgeFilter || edgeFilter.size === 0 || edgeFilter.has(e.edge_type))
    .map<ElementDefinition>((e) => ({
      data: {
        id: `${e.source}->${e.target}:${e.edge_type}`,
        source: e.source,
        target: e.target,
        edgeType: e.edge_type,
        weight: e.weight,
      },
    }));
  return [...nodes, ...edges];
}

export function CytoscapeView({ payload, onNodeClick, highlightedIds, nodeTypeFilter, edgeTypeFilter }: CytoscapeViewProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const cyRef = React.useRef<Core | null>(null);

  React.useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      elements: toElements(payload, nodeTypeFilter, edgeTypeFilter),
      style: cytoscapeStylesheet(),
      minZoom: 0.15,
      maxZoom: 3,
      wheelSensitivity: 0.3,
    });
    cyRef.current = cy;

    cy.layout(FCOSE_LAYOUT).run();
    cy.one("layoutstop", () => cy.fit(undefined, 40));

    cy.on("tap", "node", (evt) => onNodeClick?.(evt.target.id()));

    return () => {
      cy.destroy();
      cyRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [payload]);

  // Apply filter changes without full rebuild
  React.useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().remove();
      cy.add(toElements(payload, nodeTypeFilter, edgeTypeFilter));
    });
    cy.layout(FCOSE_LAYOUT).run();
  }, [nodeTypeFilter, edgeTypeFilter, payload]);

  // Highlight
  React.useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().removeClass("dim highlighted");
      if (highlightedIds && highlightedIds.length > 0) {
        const set = new Set(highlightedIds);
        cy.nodes().forEach((n) => {
          if (set.has(n.id())) n.addClass("highlighted");
          else n.addClass("dim");
        });
        cy.edges().forEach((e) => {
          if (set.has(e.source().id()) && set.has(e.target().id())) return;
          e.addClass("dim");
        });
      }
    });
  }, [highlightedIds]);

  return <div ref={containerRef} className="cy-canvas" />;
}
