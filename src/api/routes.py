"""
FastAPI route definitions for the KB Knowledge Graph API.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.graph_builder.builder import KnowledgeGraph
from src.models.nodes import NodeType

logger = logging.getLogger(__name__)

router = APIRouter()

# Sub-routers (config, upload, build, viz) are included at the bottom of this
# module after _get_kg is defined so they can import it safely.

# KG instance injected at startup
_kg: KnowledgeGraph | None = None


def set_knowledge_graph(kg: KnowledgeGraph) -> None:
    global _kg
    _kg = kg


def _get_kg() -> KnowledgeGraph:
    if _kg is None:
        raise HTTPException(status_code=503, detail="Knowledge graph not loaded.")
    return _kg


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    stream: bool = False


class QueryResponse(BaseModel):
    query: str
    intent: str
    kb_focus: str
    extracted_topics: list[str]
    response: str
    steps: list[dict[str, Any]]
    tools_referenced: list[dict[str, Any]]
    knowledge_concepts: list[dict[str, Any]]
    follow_up_suggestions: list[str]
    traversal_path: list[str]
    error: Optional[str] = None


class TraverseRequest(BaseModel):
    node_id: str
    max_depth: int = 3
    edge_types: Optional[list[str]] = None


class TraverseResponse(BaseModel):
    root_node_id: str
    traversal_path: list[str]
    nodes: list[dict[str, Any]]
    edge_count: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/query", response_model=QueryResponse, summary="Query the knowledge graph")
async def query_graph(req: QueryRequest):
    """
    Main query endpoint. Runs the full LangGraph agent pipeline and
    returns a step-by-step response with tool details and follow-ups.
    """
    from src.agent.graph import KBGraphAgent

    kg = _get_kg()
    agent = KBGraphAgent.from_graph(kg)

    try:
        state = agent.query(req.query)
    except Exception as exc:
        logger.error(f"Agent query failed: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))

    return QueryResponse(
        query=state["query"],
        intent=state.get("intent", ""),
        kb_focus=state.get("kb_focus", "both"),
        extracted_topics=state.get("extracted_topics", []),
        response=state.get("response", ""),
        steps=state.get("steps", []),
        tools_referenced=state.get("tools_referenced", []),
        knowledge_concepts=state.get("knowledge_concepts", []),
        follow_up_suggestions=state.get("follow_up_suggestions", []),
        traversal_path=state.get("traversal_path", []),
        error=state.get("error"),
    )


@router.get("/graph/node/{node_id}", summary="Get node details and edges")
async def get_node(node_id: str):
    """Return a node's data along with its incoming and outgoing edges."""
    kg = _get_kg()
    node = kg.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found.")

    edges = kg.get_edges(node_id)
    children = kg.get_children(node_id)

    return {
        "node": node.to_dict(),
        "edges": edges,
        "children": children,
        "parent": node.parent_id,
    }


@router.get("/graph/tree", summary="Get full graph tree for navigation")
async def get_tree(max_depth: int = Query(default=2, ge=1, le=4)):
    """
    Return the two root domain nodes with their subtrees.
    Used by a front-end tree navigator.
    """
    kg = _get_kg()
    tree = kg.full_tree()
    return {"tree": tree}


@router.get("/graph/tools", summary="List all extracted tools")
async def get_tools(
    category: Optional[str] = None,
    provider: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    """
    Return all ToolNode objects, optionally filtered by category, provider, or search term.
    """
    from src.models.nodes import ToolNode

    kg = _get_kg()
    tools = [
        node.to_dict()
        for node in kg.nodes.values()
        if isinstance(node, ToolNode)
    ]

    if category:
        tools = [t for t in tools if category.lower() in t.get("category", "").lower()]
    if provider:
        tools = [t for t in tools if provider.lower() in t.get("provider", "").lower()]
    if search:
        q = search.lower()
        tools = [
            t for t in tools
            if q in t.get("tool_name", "").lower()
            or q in t.get("purpose", "").lower()
            or q in t.get("category", "").lower()
        ]

    return {
        "total": len(tools),
        "tools": tools[:limit],
    }


@router.post("/graph/traverse", response_model=TraverseResponse, summary="Traverse from a node")
async def traverse_from_node(req: TraverseRequest):
    """
    BFS traverse from a given node with optional edge-type filter.
    Returns ordered list of visited nodes with their data.
    """
    kg = _get_kg()
    if req.node_id not in kg.G:
        raise HTTPException(status_code=404, detail=f"Node '{req.node_id}' not found.")

    path = kg.bfs_traverse(
        [req.node_id],
        max_depth=req.max_depth,
        edge_types=req.edge_types,
        max_nodes=60,
    )

    nodes_data = []
    for nid in path:
        node = kg.nodes.get(nid)
        if node:
            nodes_data.append(node.to_dict())

    # Count edges within traversal set
    path_set = set(path)
    edge_count = sum(
        1 for u, v in kg.G.edges()
        if u in path_set and v in path_set
    )

    return TraverseResponse(
        root_node_id=req.node_id,
        traversal_path=path,
        nodes=nodes_data,
        edge_count=edge_count,
    )


@router.get("/graph/search", summary="Hybrid search across nodes")
async def search_nodes(
    q: str = Query(..., min_length=2),
    top_k: int = Query(default=10, ge=1, le=50),
    node_type: Optional[str] = None,
):
    """Search for nodes using hybrid semantic + keyword search."""
    kg = _get_kg()

    node_types = None
    if node_type:
        try:
            node_types = [NodeType(node_type)]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid node_type: {node_type}")

    if node_types:
        results = kg.keyword_search(q, top_k=top_k, node_types=node_types)
    else:
        results = kg.hybrid_search(q, top_k=top_k)

    output = []
    for nid, score in results:
        node = kg.nodes.get(nid)
        if node:
            nd = node.to_dict()
            nd["search_score"] = round(score, 4)
            output.append(nd)

    return {"query": q, "results": output}


@router.get("/graph/stats", summary="Graph statistics")
async def get_stats():
    """Return node/edge counts by type."""
    kg = _get_kg()
    return kg.stats()


@router.get("/graph/chapters", summary="List all chapters from both KBs")
async def get_chapters():
    """Return all ChapterNode objects from both KBs."""
    from src.models.nodes import ChapterNode

    kg = _get_kg()
    chapters = [
        node.to_dict()
        for node in kg.nodes.values()
        if isinstance(node, ChapterNode)
    ]
    chapters.sort(key=lambda c: (c["kb_source"], c.get("chapter_num", 0)))
    return {"chapters": chapters}


# ---------------------------------------------------------------------------
# Sub-routers: upload, config, build, visualization
# Imported lazily at module bottom to avoid circular imports.
# ---------------------------------------------------------------------------

from src.api.upload_routes import router as upload_router  # noqa: E402
from src.api.config_routes import router as config_router  # noqa: E402
from src.api.build_routes import router as build_router    # noqa: E402
from src.api.viz_routes import router as viz_router        # noqa: E402

router.include_router(upload_router, tags=["kb"])
router.include_router(config_router, tags=["config"])
router.include_router(build_router, tags=["build"])
router.include_router(viz_router, tags=["graph"])
