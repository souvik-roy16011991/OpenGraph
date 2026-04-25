"""
FastAPI route definitions for the KB Knowledge Graph API.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.auth import require_user
from src.api.deps import require_workspace_id
from src.graph_builder.builder import KnowledgeGraph
from src.infra.audit import record_audit
from src.infra.db_models import User
from src.models.nodes import NodeType

logger = logging.getLogger(__name__)

router = APIRouter()

# Sub-routers (config, upload, build, viz) are included at the bottom of this
# module after _get_kg is defined so they can import it safely.

# Per-workspace KG cache — bounded LRU for horizontal-scaling safety.
#
# Each API worker / instance holds its own cache. On a cache miss the
# worker lazy-loads the graph from Memgraph (authoritative store).
#
# Cache coherence across workers: when any build completes on any worker,
# other workers still hold a stale ``KnowledgeGraph`` object for that
# workspace. We detect this by comparing the cached entry's ``loaded_at``
# against ``MAX(finished_at)`` for completed builds on that workspace, and
# reload on mismatch. The freshness check is itself cached for
# ``_FRESHNESS_TTL_SECONDS`` to avoid hitting Neon on every chat query.
import time as _time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional as _Optional

_KG_CACHE_MAX = 50                  # evict oldest when >50 workspaces cached
_FRESHNESS_TTL_SECONDS = 30.0       # debounce build-freshness checks

@dataclass
class _CachedKG:
    kg: KnowledgeGraph
    loaded_at_unix: float            # wall-clock time the KG was hydrated
    latest_build_unix: float         # MAX(finished_at) at load-time (epoch seconds)
    freshness_checked_at: float      # monotonic clock of last freshness check

_kg_by_ws: "OrderedDict[str, _CachedKG]" = OrderedDict()


def _evict_lru_if_needed() -> None:
    while len(_kg_by_ws) > _KG_CACHE_MAX:
        _kg_by_ws.popitem(last=False)


def set_knowledge_graph(kg: KnowledgeGraph, workspace_id: str | None = None) -> None:
    """Install *kg* in the per-workspace cache.

    If workspace_id is omitted, it is read off ``kg.workspace_id`` (always
    populated by build_graph and KnowledgeGraph.load).
    """
    wid = workspace_id or getattr(kg, "workspace_id", None)
    if not wid:
        raise ValueError("set_knowledge_graph: workspace_id is required.")
    now = _time.time()
    _kg_by_ws[wid] = _CachedKG(
        kg=kg,
        loaded_at_unix=now,
        # latest_build_unix unknown at set time — treat as now.
        latest_build_unix=now,
        freshness_checked_at=_time.monotonic(),
    )
    _kg_by_ws.move_to_end(wid)
    _evict_lru_if_needed()


async def _latest_done_build_unix(workspace_id: str) -> _Optional[float]:
    """Read MAX(finished_at) of completed builds for this workspace.

    Returns the epoch-seconds float, or None if no done build exists.
    """
    from sqlalchemy import func as _func
    from sqlalchemy import select as _select
    from src.infra.db import get_session as _get_session
    from src.infra.db_models import BuildJobRow as _BuildJobRow
    import uuid as _uuid
    try:
        async with _get_session() as s:
            r = (await s.execute(
                _select(_func.max(_BuildJobRow.finished_at))
                .where(_BuildJobRow.workspace_id == _uuid.UUID(workspace_id))
                .where(_BuildJobRow.status == "done")
            )).scalar_one_or_none()
            if r is None:
                return None
            return r.timestamp()
    except Exception as exc:
        logger.debug("latest_done_build lookup failed for %s: %s", workspace_id, exc)
        return None


async def _get_kg(workspace_id: str) -> KnowledgeGraph:
    """Return the live KnowledgeGraph for *workspace_id*, loading from Memgraph if needed.

    Cache miss path: hydrate from Memgraph and store in the per-worker LRU.
    Cache hit path: check freshness against Neon's build history (debounced
    to ``_FRESHNESS_TTL_SECONDS``) and reload if a newer build has landed
    on another worker.

    Must be async because FastAPI route handlers run inside an already-
    active event loop — attempting ``asyncio.run`` / ``loop.run_until_complete``
    from within an async request throws ``RuntimeError: this event loop is
    already running``. All callers are async route handlers; they ``await``.
    """
    cached = _kg_by_ws.get(workspace_id)
    if cached is not None:
        now_mono = _time.monotonic()
        if now_mono - cached.freshness_checked_at < _FRESHNESS_TTL_SECONDS:
            _kg_by_ws.move_to_end(workspace_id)
            return cached.kg
        # Debounce window elapsed — check Neon directly (already in a loop).
        latest = await _latest_done_build_unix(workspace_id)
        cached.freshness_checked_at = now_mono
        if latest is None or latest <= cached.latest_build_unix:
            _kg_by_ws.move_to_end(workspace_id)
            return cached.kg
        # Newer build on another worker → evict and fall through to reload.
        logger.info(
            "KG cache stale for ws=%s (newer build at %.0f); reloading.",
            workspace_id, latest,
        )
        _kg_by_ws.pop(workspace_id, None)

    # Cache miss — lazy load from Memgraph (authoritative store).
    try:
        kg = KnowledgeGraph.load(workspace_id)
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail=(
                f"No graph built for workspace {workspace_id}. "
                "Upload KB files and POST /api/v1/build first."
            ),
        )

    latest = await _latest_done_build_unix(workspace_id) or _time.time()
    _kg_by_ws[workspace_id] = _CachedKG(
        kg=kg,
        loaded_at_unix=_time.time(),
        latest_build_unix=latest,
        freshness_checked_at=_time.monotonic(),
    )
    _kg_by_ws.move_to_end(workspace_id)
    _evict_lru_if_needed()
    return kg


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    query: str
    stream: bool = False
    session_id: Optional[str] = None
    # Optional per-request override for the LLM. When omitted, resolution
    # falls through: workspace default (graph_config.llm_model) -> env
    # LLM_MODEL. The frontend populates this from the model dropdown.
    llm_model: Optional[str] = None


class ChatUsage(BaseModel):
    """Per-turn LLM usage rollup surfaced on ``QueryResponse``. Sums across
    every ``llm_invoke`` that fired during the turn (intent classify + answer
    synthesis + any future tool-reasoning nodes)."""
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_total_tokens: int = 0
    llm_calls: int = 0
    model: Optional[str] = None


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
    session_id: Optional[str] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    # The resolved OpenRouter model id that actually answered (request
    # override > workspace default > env LLM_MODEL). Surfaced so the UI can
    # label each turn with the model that produced it — the answer to
    # "which model said this?" must come from the server, not from whatever
    # the user has selected in the dropdown right now.
    llm_model: Optional[str] = None
    # Token usage for this turn. Written by the chat handler after the agent
    # completes from the per-turn ``chat_metrics_var`` accumulator. Absent
    # (None) for pre-feature rows — the UI renders "—".
    usage: Optional[ChatUsage] = None
    # True if both the user and assistant turn were written to Neon
    # ``chat_messages``. When False the answer was served successfully but
    # the server could not record the turn — the UI should surface a subtle
    # "history not saved" hint so the user knows to retry if they want a
    # durable record.
    history_persisted: bool = True


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
async def query_graph(
    req: QueryRequest,
    workspace_id: str = Depends(require_workspace_id),
    user: User = Depends(require_user),
):
    """Main query endpoint for the interactive (JWT-auth) UI.

    Runs the LangGraph agent pipeline. All pipeline logic — agent execution,
    chat persistence, audit, billing — lives in
    :func:`src.api.query_service.run_query` so the public ``/api/v1/ext/query``
    endpoint can share it byte-for-byte. This wrapper only resolves the
    caller's identity and delegates.
    """
    from src.api.query_service import run_query
    return await run_query(
        workspace_id=workspace_id,
        req=req,
        owner_user_id=user.id,
        actor_type="user",
        audit_action="chat.query",
    )


async def _persist_chat_turn(
    workspace_id: str,
    session_id: str,
    query: str,
    resp: QueryResponse | None,
    duration_ms: int,
    error: Optional[str] = None,
) -> None:
    """Upsert chat_sessions row and append two chat_messages (user + assistant)."""
    import uuid as _uuid
    from datetime import datetime, timezone
    from sqlalchemy.dialects.postgresql import insert
    from src.infra.db import get_session
    from src.infra.db_models import ChatMessage, ChatSession

    try:
        sid = _uuid.UUID(session_id)
    except ValueError:
        sid = _uuid.uuid4()

    # Title: first 60 chars of the user's first query for this session.
    title = (query or "").strip()[:60] or None

    async with get_session() as s:
        now = datetime.now(timezone.utc)
        sess_stmt = insert(ChatSession).values(
            session_id=sid,
            workspace_id=_uuid.UUID(workspace_id),
            title=title, created_at=now, last_activity_at=now,
        ).on_conflict_do_update(
            index_elements=["session_id"],
            set_={"last_activity_at": now},
        )
        await s.execute(sess_stmt)

        # User turn
        s.add(ChatMessage(
            session_id=sid, role="user", query=query, duration_ms=0,
        ))
        # Assistant turn
        if resp is not None:
            s.add(ChatMessage(
                session_id=sid,
                role="assistant",
                response=resp.model_dump(),
                intent=resp.intent,
                kb_focus=resp.kb_focus,
                extracted_topics=resp.extracted_topics,
                tools_referenced=resp.tools_referenced,
                knowledge_concepts=resp.knowledge_concepts,
                traversal_path=resp.traversal_path,
                follow_up_suggestions=resp.follow_up_suggestions,
                error=resp.error,
                duration_ms=duration_ms,
            ))
        else:
            s.add(ChatMessage(
                session_id=sid, role="assistant",
                error=error, duration_ms=duration_ms,
            ))
        await s.commit()


@router.get("/graph/node/{node_id}", summary="Get node details and edges")
async def get_node(node_id: str, workspace_id: str = Depends(require_workspace_id)):
    """Return a node's data along with its incoming and outgoing edges."""
    kg = await _get_kg(workspace_id)
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
async def get_tree(
    max_depth: int = Query(default=2, ge=1, le=4),
    workspace_id: str = Depends(require_workspace_id),
):
    """Return the root domain nodes with their subtrees for UI navigation."""
    kg = await _get_kg(workspace_id)
    tree = kg.full_tree()
    return {"tree": tree}


@router.get("/graph/tools", summary="List all extracted tools")
async def get_tools(
    category: Optional[str] = None,
    provider: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    workspace_id: str = Depends(require_workspace_id),
):
    """
    Return all ToolNode objects, optionally filtered by category, provider, or search term.
    """
    from src.models.nodes import ToolNode

    kg = await _get_kg(workspace_id)
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
async def traverse_from_node(
    req: TraverseRequest,
    workspace_id: str = Depends(require_workspace_id),
):
    """
    BFS traverse from a given node with optional edge-type filter.
    Returns ordered list of visited nodes with their data.
    """
    kg = await _get_kg(workspace_id)
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
    workspace_id: str = Depends(require_workspace_id),
):
    """Search for nodes using hybrid semantic + keyword search."""
    kg = await _get_kg(workspace_id)

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
async def get_stats(workspace_id: str = Depends(require_workspace_id)):
    """Return node/edge counts by type."""
    kg = await _get_kg(workspace_id)
    return kg.stats()


@router.get("/graph/chapters", summary="List all chapters from both KBs")
async def get_chapters(workspace_id: str = Depends(require_workspace_id)):
    """Return all ChapterNode objects from both KBs."""
    from src.models.nodes import ChapterNode

    kg = await _get_kg(workspace_id)
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

from src.api.upload_routes import router as upload_router        # noqa: E402
from src.api.config_routes import router as config_router        # noqa: E402
from src.api.build_routes import router as build_router          # noqa: E402
from src.api.viz_routes import router as viz_router              # noqa: E402
from src.api.history_routes import router as history_router      # noqa: E402
from src.api.workspace_routes import router as workspace_router  # noqa: E402
from src.api.llm_routes import router as llm_router              # noqa: E402
from src.api.template_routes import router as template_router    # noqa: E402
from src.api.me_routes import router as me_router                # noqa: E402
from src.api.auth_routes import router as auth_router            # noqa: E402
from src.api.signup_otp import router as signup_otp_router       # noqa: E402
from src.api.oauth_github import router as oauth_github_router   # noqa: E402

router.include_router(workspace_router, tags=["workspace"])
router.include_router(upload_router, tags=["kb"])
router.include_router(config_router, tags=["config"])
router.include_router(build_router, tags=["build"])
router.include_router(viz_router, tags=["graph"])
router.include_router(history_router, tags=["history"])
router.include_router(llm_router, tags=["llm"])
router.include_router(template_router, tags=["templates"])
router.include_router(me_router, tags=["me"])
router.include_router(signup_otp_router)
router.include_router(auth_router)
router.include_router(oauth_github_router)
