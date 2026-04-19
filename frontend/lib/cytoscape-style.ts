import type cytoscape from "cytoscape";

export const NODE_TYPE_COLORS: Record<string, { bg: string; border: string }> = {
  domain:   { bg: "#6366f1", border: "#4338ca" },  // indigo
  chapter:  { bg: "#0ea5e9", border: "#0369a1" },  // sky
  section:  { bg: "#64748b", border: "#334155" },  // slate
  table:    { bg: "#f59e0b", border: "#b45309" },  // amber
  tool:     { bg: "#10b981", border: "#047857" },  // emerald
  process:  { bg: "#8b5cf6", border: "#6d28d9" },  // violet
  glossary: { bg: "#f43f5e", border: "#be123c" },  // rose
  concept:  { bg: "#14b8a6", border: "#0f766e" },  // teal
};

export const EDGE_TYPE_COLORS: Record<string, string> = {
  CONTAINS:        "#94a3b8",
  HAS_CONTENT:     "#cbd5e1",
  NEXT_STEP:       "#8b5cf6",
  INTEGRATES_WITH: "#10b981",
  IMPLEMENTS:      "#0ea5e9",
  USES_TOOL:       "#f59e0b",
  RELATED_TO:      "#e879f9",
  DEFINED_IN:      "#64748b",
};

export const EDGE_TYPE_STYLES: Record<string, { line: "solid" | "dashed" | "dotted"; width: number }> = {
  CONTAINS:        { line: "solid",  width: 1.5 },
  HAS_CONTENT:     { line: "solid",  width: 1 },
  NEXT_STEP:       { line: "solid",  width: 2 },
  INTEGRATES_WITH: { line: "solid",  width: 1.75 },
  IMPLEMENTS:      { line: "solid",  width: 2.5 },
  USES_TOOL:       { line: "dashed", width: 1.25 },
  RELATED_TO:      { line: "dotted", width: 1 },
  DEFINED_IN:      { line: "dashed", width: 1 },
};

export function cytoscapeStylesheet(): cytoscape.StylesheetJson {
  const nodeSelectors: cytoscape.StylesheetJson = Object.entries(NODE_TYPE_COLORS).map(
    ([type, c]) => ({
      selector: `node[type = "${type}"]`,
      style: {
        "background-color": c.bg,
        "border-color": c.border,
        "border-width": 2,
        label: "data(label)",
        "color": "#0f172a",
        "text-outline-color": "#ffffff",
        "text-outline-width": 2,
        "font-size": 10,
        "text-valign": "bottom",
        "text-margin-y": 4,
        width: 24,
        height: 24,
      },
    })
  );
  const edgeSelectors: cytoscape.StylesheetJson = Object.entries(EDGE_TYPE_STYLES).map(
    ([type, s]) => ({
      selector: `edge[edgeType = "${type}"]`,
      style: {
        "line-color": EDGE_TYPE_COLORS[type] || "#94a3b8",
        "target-arrow-color": EDGE_TYPE_COLORS[type] || "#94a3b8",
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.9,
        "line-style": s.line,
        width: s.width,
        "curve-style": "bezier",
        opacity: 0.75,
      },
    })
  );
  return [
    {
      selector: "node",
      style: {
        "background-color": "#64748b",
        "border-color": "#334155",
        "border-width": 2,
        label: "data(label)",
      },
    },
    {
      selector: "node.dim",
      style: { opacity: 0.15 },
    },
    {
      selector: "node.highlighted",
      style: {
        "border-width": 4,
        "border-color": "#f97316",
        width: 30,
        height: 30,
      },
    },
    {
      selector: "edge",
      style: {
        "line-color": "#cbd5e1",
        width: 1,
        "curve-style": "bezier",
      },
    },
    {
      selector: "edge.dim",
      style: { opacity: 0.08 },
    },
    ...nodeSelectors,
    ...edgeSelectors,
  ];
}

export const FCOSE_LAYOUT: cytoscape.LayoutOptions = {
  name: "fcose",
  animate: false,
  randomize: true,
  nodeRepulsion: 6500,
  idealEdgeLength: 70,
  padding: 30,
  tile: true,
  quality: "default",
} as cytoscape.LayoutOptions;
