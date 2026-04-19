"""
Graph visualization endpoint.

Returns a flat {nodes, edges} payload suitable for Cytoscape.js / D3. Supports
filtering by node_types, edge_types, kb_source, and a max_nodes cap.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from src.api.routes import _get_kg

router = APIRouter()


def _csv_list(value: Optional[str]) -> Optional[list[str]]:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


@router.get("/graph/visualization", summary="Flat nodes + edges payload for graph viz")
async def get_visualization(
    node_types: Optional[str] = Query(default=None, description="CSV of node types to include"),
    edge_types: Optional[str] = Query(default=None, description="CSV of edge types to include"),
    kb_source: Optional[str] = Query(default=None, description="'knowledge' or 'tool'"),
    max_nodes: int = Query(default=2000, ge=1, le=20000),
):
    kg = _get_kg()

    node_type_filter = _csv_list(node_types)
    edge_type_filter = _csv_list(edge_types)

    nodes_out: list[dict] = []
    included_ids: set[str] = set()

    for nid, node in kg.nodes.items():
        if len(nodes_out) >= max_nodes:
            break
        nt = node.node_type.value if hasattr(node.node_type, "value") else str(node.node_type)
        if node_type_filter and nt not in node_type_filter:
            continue
        ks = node.kb_source.value if hasattr(node.kb_source, "value") else str(node.kb_source)
        if kb_source and ks != kb_source:
            continue

        nodes_out.append({
            "id": nid,
            "type": nt,
            "kb_source": ks,
            "heading": node.heading,
            "summary": getattr(node, "content_summary", "") or "",
            "level": getattr(node, "level", 0),
            "parent_id": getattr(node, "parent_id", None),
        })
        included_ids.add(nid)

    edges_out: list[dict] = []
    for src, tgt, data in kg.G.edges(data=True):
        if src not in included_ids or tgt not in included_ids:
            continue
        et = data.get("edge_type", "unknown")
        if edge_type_filter and et not in edge_type_filter:
            continue
        edges_out.append({
            "source": src,
            "target": tgt,
            "edge_type": et,
            "weight": float(data.get("weight", 1.0)),
        })

    return {
        "nodes": nodes_out,
        "edges": edges_out,
        "total_nodes": len(kg.nodes),
        "total_edges": kg.G.number_of_edges(),
        "truncated": len(nodes_out) >= max_nodes,
    }
