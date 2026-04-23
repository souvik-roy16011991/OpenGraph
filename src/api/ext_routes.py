"""
Public developer API at /api/v1/ext/*.

Auth model:
  Authorization: Bearer og_live_<secret>      # API key
  (JWT tokens are rejected — use the internal /api/v1/* endpoints for
  browser sessions.)

Every route depends on :func:`rate_limit_per_key`, which itself depends on
:func:`require_api_key` — so the dependency chain is:

    HTTP request
     ─► require_api_key     401 if bad key
     ─► rate_limit_per_key  429 if over quota, otherwise returns ApiKeyContext
     ─► _resolve_workspace  verifies the caller owns the target workspace
     ─► internal helper     _get_kg, kg.search, etc.

Workspace ownership check: the API key either pins a workspace
(``ctx.workspace_id`` set) or allows any workspace the key's ``user_id``
owns. Either way, the target workspace uuid is resolved once per request
and verified before touching any side store.

Response shapes live in :mod:`src.api.ext_models`. They deliberately strip
or hide internal-only fields so the SDK surface stays stable as the
LangGraph agent evolves behind the scenes.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from src.api.api_key_auth import ApiKeyContext
from src.api.ext_models import (
    ExtBuildJobSummary,
    ExtBuildJobsResponse,
    ExtChatMessage,
    ExtChatSessionDetail,
    ExtChatSessionSummary,
    ExtChatSessionsResponse,
    ExtChatUsage,
    ExtGraphStats,
    ExtNodeDetail,
    ExtQueryRequest,
    ExtQueryResponse,
    ExtSearchResponse,
    ExtSearchResult,
    ExtTraverseRequest,
    ExtTraverseResponse,
)
from src.api.query_service import run_query
from src.api.routes import (
    ChatUsage,
    QueryRequest,
    QueryResponse,
    _get_kg,
)
from src.config import USE_NEON
from src.infra.db import get_session
from src.infra.db_models import (
    BuildJobRow,
    ChatMessage,
    ChatSession,
    Workspace,
)
from src.infra.rate_limit import rate_limit_per_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ext", tags=["ext"])


# ---------------------------------------------------------------------------
# Workspace resolution — shared by every /ext route that needs a workspace
# ---------------------------------------------------------------------------

async def _resolve_workspace(
    ctx: ApiKeyContext,
    requested_workspace_id: Optional[str],
) -> uuid.UUID:
    """Pick the target workspace UUID for this request and verify ownership.

    Resolution rules:
      - If the key is pinned to a specific workspace (``ctx.workspace_id``
        is not None), the caller may omit the header/body field entirely;
        we use the pinned value. If they also pass a different value,
        we 404 rather than silently override — avoids subtle surprise.
      - If the key is account-scoped (``ctx.workspace_id`` is None), the
        caller MUST pass a ``workspace_id`` and we verify it's owned by
        ``ctx.user_id``. Non-owned workspaces return 404 (not 403) to
        avoid existence leakage — matches the JWT path.
    """
    if ctx.workspace_id is not None:
        pinned = ctx.workspace_id
        if requested_workspace_id and requested_workspace_id.lower() != str(pinned).lower():
            raise HTTPException(
                status_code=404,
                detail="Workspace not found.",
            )
        return pinned

    if not requested_workspace_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "workspace_id is required for account-scoped API keys. "
                "Pass ``workspace_id`` in the body (POST) or query string (GET)."
            ),
        )

    try:
        wid = uuid.UUID(requested_workspace_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="workspace_id must be a UUID.")

    if not USE_NEON:
        raise HTTPException(
            status_code=503,
            detail="Workspace ownership check requires DATABASE_URL (Neon).",
        )

    async with get_session() as s:
        owner = (await s.execute(
            select(Workspace.user_id).where(
                Workspace.id == wid,
                Workspace.deleted_at.is_(None),
            )
        )).scalar_one_or_none()
    if owner is None or owner != ctx.user_id:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return wid


# ---------------------------------------------------------------------------
# POST /ext/query
# ---------------------------------------------------------------------------

@router.post("/query", response_model=ExtQueryResponse, summary="Query the knowledge graph")
async def ext_query(
    req: ExtQueryRequest,
    ctx: ApiKeyContext = Depends(rate_limit_per_key),
) -> ExtQueryResponse:
    """Run the knowledge-graph agent against *workspace_id* and return the
    assistant's answer plus light metadata.

    Uses the same pipeline as the internal ``POST /query`` — identical
    chat persistence, billing, and audit semantics — so answers are
    consistent whether they come from the browser UI or a cron job.
    """
    wid = await _resolve_workspace(ctx, req.workspace_id)

    internal_req = QueryRequest(
        query=req.query,
        session_id=req.session_id,
        llm_model=req.llm_model,
    )
    internal_resp: QueryResponse = await run_query(
        workspace_id=str(wid),
        req=internal_req,
        owner_user_id=ctx.user_id,
        actor_type="api_key",
        audit_action="api.query",
        audit_metadata_extra={"api_key_id": str(ctx.key_id)},
        billing_source_type="api_key",
        billing_reason="chat_api",
    )

    usage = None
    if internal_resp.usage is not None:
        usage = ExtChatUsage(**internal_resp.usage.model_dump())

    return ExtQueryResponse(
        session_id=internal_resp.session_id or "",
        response=internal_resp.response,
        intent=internal_resp.intent,
        kb_focus=internal_resp.kb_focus,
        extracted_topics=internal_resp.extracted_topics,
        follow_up_suggestions=internal_resp.follow_up_suggestions,
        llm_model=internal_resp.llm_model,
        usage=usage,
        duration_ms=internal_resp.duration_ms or 0,
        history_persisted=internal_resp.history_persisted,
        steps=internal_resp.steps if req.debug else None,
        traversal_path=internal_resp.traversal_path if req.debug else None,
        knowledge_concepts=internal_resp.knowledge_concepts if req.debug else None,
        tools_referenced=internal_resp.tools_referenced if req.debug else None,
    )


# ---------------------------------------------------------------------------
# GET /ext/graph/stats
# ---------------------------------------------------------------------------

@router.get("/graph/stats", response_model=ExtGraphStats, summary="Graph statistics")
async def ext_graph_stats(
    workspace_id: Optional[str] = Query(default=None),
    ctx: ApiKeyContext = Depends(rate_limit_per_key),
) -> ExtGraphStats:
    wid = await _resolve_workspace(ctx, workspace_id)
    kg = await _get_kg(str(wid))
    stats = kg.stats()
    return ExtGraphStats(
        total_nodes=int(stats.get("total_nodes", 0) or 0),
        total_edges=int(stats.get("total_edges", 0) or 0),
        nodes_by_type=stats.get("nodes_by_type") or {},
        edges_by_type=stats.get("edges_by_type") or {},
        backends=stats.get("backends") or {},
    )


# ---------------------------------------------------------------------------
# GET /ext/graph/search
# ---------------------------------------------------------------------------

@router.get("/graph/search", response_model=ExtSearchResponse, summary="Hybrid search")
async def ext_graph_search(
    q: str = Query(description="Search query text."),
    workspace_id: Optional[str] = Query(default=None),
    top_k: int = Query(default=10, ge=1, le=50),
    node_type: Optional[str] = Query(default=None),
    ctx: ApiKeyContext = Depends(rate_limit_per_key),
) -> ExtSearchResponse:
    wid = await _resolve_workspace(ctx, workspace_id)
    kg = await _get_kg(str(wid))
    hits = kg.hybrid_search(q, top_k=top_k, node_type=node_type)
    results = []
    for h in hits:
        node = kg.get_node(h.get("node_id"))
        if not node:
            continue
        results.append(ExtSearchResult(
            node_id=node.node_id,
            node_type=node.node_type.value,
            heading=node.heading,
            kb_source=node.kb_source.value,
            score=float(h.get("search_score", 0.0) or 0.0),
        ))
    return ExtSearchResponse(query=q, results=results)


# ---------------------------------------------------------------------------
# GET /ext/graph/node/{node_id}
# ---------------------------------------------------------------------------

@router.get("/graph/node/{node_id}", response_model=ExtNodeDetail, summary="Get node detail")
async def ext_graph_node(
    node_id: str,
    workspace_id: Optional[str] = Query(default=None),
    ctx: ApiKeyContext = Depends(rate_limit_per_key),
) -> ExtNodeDetail:
    wid = await _resolve_workspace(ctx, workspace_id)
    kg = await _get_kg(str(wid))
    node = kg.get_node(node_id)
    if not node:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found.")
    return ExtNodeDetail(
        node=node.to_dict(),
        edges=kg.get_edges(node_id),
        children=kg.get_children(node_id),
        parent=node.parent_id,
    )


# ---------------------------------------------------------------------------
# POST /ext/graph/traverse
# ---------------------------------------------------------------------------

@router.post("/graph/traverse", response_model=ExtTraverseResponse, summary="BFS traverse")
async def ext_graph_traverse(
    req: ExtTraverseRequest,
    ctx: ApiKeyContext = Depends(rate_limit_per_key),
) -> ExtTraverseResponse:
    wid = await _resolve_workspace(ctx, req.workspace_id)
    kg = await _get_kg(str(wid))
    if req.node_id not in kg.G:
        raise HTTPException(status_code=404, detail=f"Node '{req.node_id}' not found.")
    path = kg.bfs_traverse(
        [req.node_id],
        max_depth=req.max_depth,
        edge_types=req.edge_types,
        max_nodes=60,
    )
    nodes = []
    edge_count = 0
    for nid in path:
        n = kg.get_node(nid)
        if n:
            nodes.append(n.to_dict())
        edge_count += len(kg.get_edges(nid))
    return ExtTraverseResponse(
        root_node_id=req.node_id,
        traversal_path=path,
        nodes=nodes,
        edge_count=edge_count,
    )


# ---------------------------------------------------------------------------
# GET /ext/graph/tree
# ---------------------------------------------------------------------------

@router.get("/graph/tree", summary="Hierarchical navigation tree")
async def ext_graph_tree(
    workspace_id: Optional[str] = Query(default=None),
    ctx: ApiKeyContext = Depends(rate_limit_per_key),
) -> dict:
    wid = await _resolve_workspace(ctx, workspace_id)
    kg = await _get_kg(str(wid))
    return {"tree": kg.full_tree()}


# ---------------------------------------------------------------------------
# GET /ext/graph/tools
# ---------------------------------------------------------------------------

@router.get("/graph/tools", summary="List extracted tool nodes")
async def ext_graph_tools(
    workspace_id: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    provider: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: ApiKeyContext = Depends(rate_limit_per_key),
) -> dict:
    wid = await _resolve_workspace(ctx, workspace_id)
    from src.models.nodes import ToolNode
    kg = await _get_kg(str(wid))
    tools = [n.to_dict() for n in kg.nodes.values() if isinstance(n, ToolNode)]
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
        ]
    return {"total": len(tools), "tools": tools[:limit]}


# ---------------------------------------------------------------------------
# GET /ext/graph/chapters
# ---------------------------------------------------------------------------

@router.get("/graph/chapters", summary="List all chapters")
async def ext_graph_chapters(
    workspace_id: Optional[str] = Query(default=None),
    ctx: ApiKeyContext = Depends(rate_limit_per_key),
) -> dict:
    wid = await _resolve_workspace(ctx, workspace_id)
    from src.models.nodes import ChapterNode
    kg = await _get_kg(str(wid))
    chapters = [n.to_dict() for n in kg.nodes.values() if isinstance(n, ChapterNode)]
    return {"chapters": chapters}


# ---------------------------------------------------------------------------
# GET /ext/history/chats
# ---------------------------------------------------------------------------

@router.get(
    "/history/chats",
    response_model=ExtChatSessionsResponse,
    summary="List chat sessions",
)
async def ext_history_chats(
    workspace_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    ctx: ApiKeyContext = Depends(rate_limit_per_key),
) -> ExtChatSessionsResponse:
    wid = await _resolve_workspace(ctx, workspace_id)
    async with get_session() as s:
        rows = (await s.execute(
            select(ChatSession)
            .where(ChatSession.workspace_id == wid)
            .order_by(ChatSession.last_activity_at.desc())
            .limit(limit)
        )).scalars().all()
        summaries = []
        for r in rows:
            count = (await s.execute(
                select(ChatMessage)
                .where(ChatMessage.session_id == r.session_id)
            )).scalars().all()
            summaries.append(ExtChatSessionSummary(
                session_id=str(r.session_id),
                title=r.title,
                message_count=len(count),
                created_at=r.created_at,
                last_activity_at=r.last_activity_at,
            ))
    return ExtChatSessionsResponse(sessions=summaries)


# ---------------------------------------------------------------------------
# GET /ext/history/chats/{session_id}
# ---------------------------------------------------------------------------

@router.get(
    "/history/chats/{session_id}",
    response_model=ExtChatSessionDetail,
    summary="Full message thread for a chat session",
)
async def ext_history_chat_detail(
    session_id: str,
    workspace_id: Optional[str] = Query(default=None),
    ctx: ApiKeyContext = Depends(rate_limit_per_key),
) -> ExtChatSessionDetail:
    wid = await _resolve_workspace(ctx, workspace_id)
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="session_id must be a UUID.")
    async with get_session() as s:
        sess = (await s.execute(
            select(ChatSession).where(
                ChatSession.session_id == sid,
                ChatSession.workspace_id == wid,
            )
        )).scalar_one_or_none()
        if sess is None:
            raise HTTPException(status_code=404, detail="Chat session not found.")
        msgs = (await s.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == sid)
            .order_by(ChatMessage.created_at.asc())
        )).scalars().all()
    return ExtChatSessionDetail(
        session_id=str(sess.session_id),
        workspace_id=str(sess.workspace_id),
        title=sess.title,
        messages=[
            ExtChatMessage(
                id=m.id,
                role=m.role,
                query=m.query,
                response=m.response,
                intent=m.intent,
                kb_focus=m.kb_focus,
                created_at=m.created_at,
                duration_ms=m.duration_ms,
            ) for m in msgs
        ],
    )


# ---------------------------------------------------------------------------
# GET /ext/history/builds
# ---------------------------------------------------------------------------

@router.get(
    "/history/builds",
    response_model=ExtBuildJobsResponse,
    summary="List recent build jobs",
)
async def ext_history_builds(
    workspace_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    ctx: ApiKeyContext = Depends(rate_limit_per_key),
) -> ExtBuildJobsResponse:
    wid = await _resolve_workspace(ctx, workspace_id)
    async with get_session() as s:
        rows = (await s.execute(
            select(BuildJobRow)
            .where(BuildJobRow.workspace_id == wid)
            .order_by(BuildJobRow.created_at.desc())
            .limit(limit)
        )).scalars().all()
    return ExtBuildJobsResponse(
        builds=[
            ExtBuildJobSummary(
                job_id=r.job_id,
                workspace_id=str(r.workspace_id),
                status=r.status,
                stage=r.stage,
                stage_name=r.stage_name,
                percent=r.percent,
                created_at=r.created_at,
                finished_at=r.finished_at,
            ) for r in rows
        ],
    )


# ---------------------------------------------------------------------------
# GET /ext/history/builds/{job_id}
# ---------------------------------------------------------------------------

@router.get(
    "/history/builds/{job_id}",
    response_model=ExtBuildJobSummary,
    summary="Detail for a single build job",
)
async def ext_history_build_detail(
    job_id: str,
    ctx: ApiKeyContext = Depends(rate_limit_per_key),
) -> ExtBuildJobSummary:
    async with get_session() as s:
        row = (await s.execute(
            select(BuildJobRow).where(BuildJobRow.job_id == job_id)
        )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Build job not found.")
    # Verify the key's owner actually owns this build's workspace.
    await _resolve_workspace(ctx, str(row.workspace_id))
    return ExtBuildJobSummary(
        job_id=row.job_id,
        workspace_id=str(row.workspace_id),
        status=row.status,
        stage=row.stage,
        stage_name=row.stage_name,
        percent=row.percent,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )
