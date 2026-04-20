"""
History (read) endpoints backed by Neon.

All endpoints return 503 when Neon is not configured.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth import require_user
from src.api.deps import require_workspace_id
from src.config import USE_NEON
from src.infra.db_models import User

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_neon() -> None:
    if not USE_NEON:
        raise HTTPException(
            status_code=503,
            detail="History is unavailable: DATABASE_URL is not configured.",
        )


# ---------------------------------------------------------------------------
# Build jobs
# ---------------------------------------------------------------------------

@router.get("/history/builds", summary="List recent build jobs")
async def list_builds(
    limit: int = Query(default=50, ge=1, le=500),
    workspace_id: str = Depends(require_workspace_id),
):
    _require_neon()
    from sqlalchemy import select
    from src.infra.db import get_session
    from src.infra.db_models import BuildJobRow

    async with get_session() as s:
        result = await s.execute(
            select(BuildJobRow)
            .where(BuildJobRow.workspace_id == uuid.UUID(workspace_id))
            .order_by(BuildJobRow.created_at.desc()).limit(limit)
        )
        rows = result.scalars().all()

    return {
        "builds": [
            {
                "job_id": r.job_id,
                "status": r.status,
                "stage": r.stage,
                "stage_name": r.stage_name,
                "percent": r.percent,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "duration_s": (
                    (r.finished_at - r.started_at).total_seconds()
                    if (r.started_at and r.finished_at) else None
                ),
                "skip_embeddings": r.skip_embeddings,
                "skip_llm_cross_links": r.skip_llm_cross_links,
                "error": (r.error or "")[:200] if r.error else None,
                "stats": r.stats,
                "backends": r.backends,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.get("/history/builds/{job_id}", summary="Detail for a single build job")
async def get_build(job_id: str, user: User = Depends(require_user)):
    _require_neon()
    from sqlalchemy import select
    from src.infra.db import get_session
    from src.infra.db_models import BuildJobRow, Workspace

    async with get_session() as s:
        r = (await s.execute(select(BuildJobRow).where(BuildJobRow.job_id == job_id))).scalar_one_or_none()
        if r is None:
            raise HTTPException(status_code=404, detail=f"Build {job_id!r} not found")
        owner_id = (await s.execute(
            select(Workspace.user_id).where(Workspace.id == r.workspace_id)
        )).scalar_one_or_none()
        if owner_id != user.id:
            # Return 404 rather than 403 to avoid cross-tenant existence leaks.
            raise HTTPException(status_code=404, detail=f"Build {job_id!r} not found")

    return {
        "job_id": r.job_id,
        "status": r.status,
        "stage": r.stage,
        "stage_name": r.stage_name,
        "percent": r.percent,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "error": r.error,
        "log_tail": r.log_tail or [],
        "skip_embeddings": r.skip_embeddings,
        "skip_llm_cross_links": r.skip_llm_cross_links,
        "domain_snapshot": r.domain_snapshot,
        "graph_snapshot": r.graph_snapshot,
        "stats": r.stats,
        "backends": r.backends,
        "created_at": r.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

@router.get("/history/chats", summary="List recent chat sessions")
async def list_chats(
    limit: int = Query(default=50, ge=1, le=500),
    workspace_id: str = Depends(require_workspace_id),
):
    _require_neon()
    from sqlalchemy import select
    from src.infra.db import get_session
    from src.infra.db_models import ChatSession, ChatMessage

    async with get_session() as s:
        result = await s.execute(
            select(ChatSession)
            .where(ChatSession.workspace_id == uuid.UUID(workspace_id))
            .order_by(ChatSession.last_activity_at.desc()).limit(limit)
        )
        sessions = result.scalars().all()

        # Count messages per session in a single roll-up query
        counts: dict[str, int] = {}
        if sessions:
            from sqlalchemy import func
            ids = [s_.session_id for s_ in sessions]
            cnt_res = await s.execute(
                select(ChatMessage.session_id, func.count(ChatMessage.id))
                .where(ChatMessage.session_id.in_(ids))
                .group_by(ChatMessage.session_id)
            )
            for sid, cnt in cnt_res.all():
                counts[str(sid)] = int(cnt)

    return {
        "chats": [
            {
                "session_id": str(r.session_id),
                "title": r.title,
                "message_count": counts.get(str(r.session_id), 0),
                "created_at": r.created_at.isoformat(),
                "last_activity_at": r.last_activity_at.isoformat(),
            }
            for r in sessions
        ]
    }


@router.get("/history/chats/{session_id}", summary="Full message thread for a session")
async def get_chat(session_id: str, user: User = Depends(require_user)):
    _require_neon()
    import uuid as _uuid
    from sqlalchemy import select
    from src.infra.db import get_session
    from src.infra.db_models import ChatSession, ChatMessage, Workspace

    try:
        sid = _uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="session_id must be a UUID")

    async with get_session() as s:
        sess = (await s.execute(select(ChatSession).where(ChatSession.session_id == sid))).scalar_one_or_none()
        if sess is None:
            raise HTTPException(status_code=404, detail="Session not found")
        owner_id = (await s.execute(
            select(Workspace.user_id).where(Workspace.id == sess.workspace_id)
        )).scalar_one_or_none()
        if owner_id != user.id:
            raise HTTPException(status_code=404, detail="Session not found")
        msgs = (await s.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == sid)
            .order_by(ChatMessage.id)
        )).scalars().all()

    return {
        "session_id": str(sess.session_id),
        "title": sess.title,
        "created_at": sess.created_at.isoformat(),
        "last_activity_at": sess.last_activity_at.isoformat(),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "query": m.query,
                "response": m.response,
                "intent": m.intent,
                "kb_focus": m.kb_focus,
                "tools_referenced": m.tools_referenced,
                "knowledge_concepts": m.knowledge_concepts,
                "traversal_path": m.traversal_path,
                "follow_up_suggestions": m.follow_up_suggestions,
                "error": m.error,
                "duration_ms": m.duration_ms,
                "created_at": m.created_at.isoformat(),
            }
            for m in msgs
        ],
    }


# ---------------------------------------------------------------------------
# Configs
# ---------------------------------------------------------------------------

@router.get("/history/configs", summary="List config version snapshots")
async def list_configs(
    kind: Optional[str] = Query(default=None, description="'domain' or 'graph'"),
    limit: int = Query(default=50, ge=1, le=500),
    workspace_id: str = Depends(require_workspace_id),
):
    _require_neon()
    from sqlalchemy import select
    from src.infra.db import get_session
    from src.infra.db_models import ConfigVersion

    async with get_session() as s:
        stmt = (
            select(ConfigVersion)
            .where(ConfigVersion.workspace_id == uuid.UUID(workspace_id))
            .order_by(ConfigVersion.created_at.desc()).limit(limit)
        )
        if kind:
            stmt = stmt.where(ConfigVersion.kind == kind)
        rows = (await s.execute(stmt)).scalars().all()

    return {
        "configs": [
            {
                "id": r.id,
                "kind": r.kind,
                "changed_sections": r.changed_sections,
                "requires_rebuild": r.requires_rebuild,
                "yaml_preview": (r.yaml_snapshot or "")[:400],
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.get("/history/configs/{config_id}", summary="Full YAML + parsed snapshot")
async def get_config(config_id: int, user: User = Depends(require_user)):
    _require_neon()
    from sqlalchemy import select
    from src.infra.db import get_session
    from src.infra.db_models import ConfigVersion, Workspace

    async with get_session() as s:
        r = (await s.execute(select(ConfigVersion).where(ConfigVersion.id == config_id))).scalar_one_or_none()
        if r is None:
            raise HTTPException(status_code=404, detail="Config version not found")
        owner_id = (await s.execute(
            select(Workspace.user_id).where(Workspace.id == r.workspace_id)
        )).scalar_one_or_none()
        if owner_id != user.id:
            raise HTTPException(status_code=404, detail="Config version not found")

    return {
        "id": r.id,
        "kind": r.kind,
        "yaml_snapshot": r.yaml_snapshot,
        "parsed_snapshot": r.parsed_snapshot,
        "changed_sections": r.changed_sections,
        "requires_rebuild": r.requires_rebuild,
        "created_at": r.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------

@router.get("/history/uploads", summary="List KB file upload history")
async def list_uploads(
    kb_source: Optional[str] = Query(default=None, description="'knowledge' or 'tool'"),
    limit: int = Query(default=50, ge=1, le=500),
    workspace_id: str = Depends(require_workspace_id),
):
    _require_neon()
    from sqlalchemy import select
    from src.infra.db import get_session
    from src.infra.db_models import KbUpload

    async with get_session() as s:
        stmt = (
            select(KbUpload)
            .where(KbUpload.workspace_id == uuid.UUID(workspace_id))
            .order_by(KbUpload.created_at.desc()).limit(limit)
        )
        if kb_source:
            stmt = stmt.where(KbUpload.kb_source == kb_source)
        rows = (await s.execute(stmt)).scalars().all()

    return {
        "uploads": [
            {
                "id": r.id,
                "kb_source": r.kb_source,
                "filename": r.filename,
                "size_bytes": r.size_bytes,
                "chapters": r.chapters,
                "title": r.title,
                "sha256": r.sha256,
                "blob_url": r.blob_url,
                "blob_error": r.blob_error,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }
