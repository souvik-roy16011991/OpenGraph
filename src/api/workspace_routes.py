"""
Workspace CRUD: list / create / detail / delete.

Phase A: every workspace belongs to a single shared "anonymous" user.
Phase B will replace ``_get_current_user_id`` with a real JWT-authenticated
user lookup.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from src.config import USE_MEMGRAPH, USE_NEON, USE_QDRANT
from src.infra.db import get_session
from src.infra.db_models import (
    ANONYMOUS_STACK_ID,
    BuildJobRow,
    ChatMessage,
    ChatSession,
    ConfigVersion,
    KbUpload,
    User,
    Workspace,
    WorkspaceFile,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: Optional[str] = None


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = None


class WorkspaceSummary(BaseModel):
    id: uuid.UUID
    name: str
    description: Optional[str]
    created_at: datetime
    updated_at: datetime
    file_counts: dict[str, int]
    last_build_at: Optional[datetime]
    last_build_status: Optional[str]
    stats: Optional[dict]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_anonymous_user_id() -> uuid.UUID:
    """Phase A: all workspaces belong to the single anonymous user.

    Auto-creates the user row if it doesn't exist.
    """
    async with get_session() as s:
        r = await s.execute(select(User).where(User.stack_user_id == ANONYMOUS_STACK_ID))
        u = r.scalar_one_or_none()
        if u is None:
            u = User(stack_user_id=ANONYMOUS_STACK_ID, display_name="Anonymous")
            s.add(u)
            await s.commit()
            await s.refresh(u)
        return u.id


async def _summary_for(workspace: Workspace, session) -> dict:
    # File counts per kb_source
    counts_res = await session.execute(
        select(WorkspaceFile.kb_source, func.count(WorkspaceFile.id))
        .where(WorkspaceFile.workspace_id == workspace.id)
        .where(WorkspaceFile.active.is_(True))
        .group_by(WorkspaceFile.kb_source)
    )
    file_counts = {"knowledge": 0, "tool": 0}
    for kb_src, cnt in counts_res.all():
        file_counts[kb_src] = int(cnt)

    # Last build
    last_build_res = await session.execute(
        select(BuildJobRow)
        .where(BuildJobRow.workspace_id == workspace.id)
        .order_by(BuildJobRow.created_at.desc())
        .limit(1)
    )
    last = last_build_res.scalar_one_or_none()

    return {
        "id": str(workspace.id),
        "name": workspace.name,
        "description": workspace.description,
        "created_at": workspace.created_at.isoformat(),
        "updated_at": workspace.updated_at.isoformat(),
        "file_counts": file_counts,
        "last_build_at": last.created_at.isoformat() if last else None,
        "last_build_status": last.status if last else None,
        "stats": last.stats if last else None,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _require_neon() -> None:
    if not USE_NEON:
        raise HTTPException(status_code=503, detail="Workspaces require DATABASE_URL (Neon).")


@router.get("/workspaces", summary="List all workspaces for the current user")
async def list_workspaces():
    _require_neon()
    user_id = await _get_anonymous_user_id()
    async with get_session() as s:
        result = await s.execute(
            select(Workspace)
            .where(Workspace.user_id == user_id)
            .order_by(Workspace.updated_at.desc())
        )
        workspaces = result.scalars().all()
        summaries = [await _summary_for(w, s) for w in workspaces]
    return {"workspaces": summaries}


@router.post("/workspaces", summary="Create a new workspace", status_code=201)
async def create_workspace(body: WorkspaceCreate):
    _require_neon()
    user_id = await _get_anonymous_user_id()
    async with get_session() as s:
        # Prevent duplicate names per user
        existing = await s.execute(
            select(Workspace).where(Workspace.user_id == user_id, Workspace.name == body.name)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail=f"Workspace named {body.name!r} already exists.")

        ws = Workspace(
            user_id=user_id,
            name=body.name,
            description=body.description,
        )
        s.add(ws)
        await s.commit()
        await s.refresh(ws)
        return await _summary_for(ws, s)


@router.get("/workspaces/{workspace_id}", summary="Get workspace detail")
async def get_workspace(workspace_id: uuid.UUID):
    _require_neon()
    async with get_session() as s:
        r = await s.execute(select(Workspace).where(Workspace.id == workspace_id))
        ws = r.scalar_one_or_none()
        if ws is None:
            raise HTTPException(status_code=404, detail="Workspace not found.")
        return await _summary_for(ws, s)


@router.patch("/workspaces/{workspace_id}", summary="Rename / update a workspace")
async def update_workspace(workspace_id: uuid.UUID, body: WorkspaceUpdate):
    _require_neon()
    async with get_session() as s:
        r = await s.execute(select(Workspace).where(Workspace.id == workspace_id))
        ws = r.scalar_one_or_none()
        if ws is None:
            raise HTTPException(status_code=404, detail="Workspace not found.")
        if body.name is not None:
            ws.name = body.name
        if body.description is not None:
            ws.description = body.description
        ws.updated_at = datetime.now(timezone.utc)
        await s.commit()
        await s.refresh(ws)
        return await _summary_for(ws, s)


@router.delete("/workspaces/{workspace_id}", summary="Delete a workspace and all its artifacts")
async def delete_workspace(workspace_id: uuid.UUID):
    """Cascades through Neon FKs; also purges Memgraph nodes, Qdrant collection,
    Vercel Blob objects, and the local /data/workspaces/{id}/ cache."""
    _require_neon()

    wid_str = str(workspace_id)

    # Best-effort cleanup in cloud stores
    if USE_MEMGRAPH:
        try:
            from src.config import MEMGRAPH_DATABASE, MEMGRAPH_PASSWORD, MEMGRAPH_URI, MEMGRAPH_USERNAME
            from src.infra.memgraph_store import MemgraphGraphStore
            store = MemgraphGraphStore(MEMGRAPH_URI, MEMGRAPH_USERNAME, MEMGRAPH_PASSWORD, MEMGRAPH_DATABASE)
            store.clear_workspace(wid_str)
        except Exception as exc:
            logger.warning("Memgraph cleanup for ws=%s failed: %s", wid_str, exc)

    if USE_QDRANT:
        try:
            from src.config import QDRANT_API_KEY, QDRANT_URL
            from qdrant_client import QdrantClient
            from src.graph_builder.builder import _short_wid
            client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30)
            collection = f"kb-{_short_wid(wid_str)}"
            try:
                client.delete_collection(collection)
            except Exception:
                pass
        except Exception as exc:
            logger.warning("Qdrant cleanup for ws=%s failed: %s", wid_str, exc)

    # Blob
    try:
        from src.infra.blob_loader import delete_workspace_blobs
        delete_workspace_blobs(wid_str)
    except Exception as exc:
        logger.warning("Blob cleanup for ws=%s failed: %s", wid_str, exc)

    # Local cache
    try:
        import shutil
        from src.config import workspace_data_dir
        shutil.rmtree(workspace_data_dir(wid_str), ignore_errors=True)
    except Exception as exc:
        logger.warning("Local cache cleanup for ws=%s failed: %s", wid_str, exc)

    # Neon rows — FK CASCADE handles children
    async with get_session() as s:
        r = await s.execute(select(Workspace).where(Workspace.id == workspace_id))
        ws = r.scalar_one_or_none()
        if ws is None:
            raise HTTPException(status_code=404, detail="Workspace not found.")
        await s.delete(ws)
        await s.commit()

    return {"ok": True, "deleted_workspace_id": wid_str}


@router.get("/workspaces/{workspace_id}/files", summary="List files in a workspace")
async def list_workspace_files(workspace_id: uuid.UUID, kb_source: Optional[str] = None):
    _require_neon()
    async with get_session() as s:
        stmt = select(WorkspaceFile).where(WorkspaceFile.workspace_id == workspace_id)
        if kb_source:
            stmt = stmt.where(WorkspaceFile.kb_source == kb_source)
        stmt = stmt.order_by(WorkspaceFile.created_at.desc())
        rows = (await s.execute(stmt)).scalars().all()
    return {
        "files": [
            {
                "id": r.id,
                "kb_source": r.kb_source,
                "filename": r.filename,
                "size_bytes": r.size_bytes,
                "chapters": r.chapters,
                "title": r.title,
                "sha256": r.sha256,
                "blob_url": r.blob_url,
                "local_path": r.local_path,
                "active": r.active,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.delete("/workspaces/{workspace_id}/files/{file_id}", summary="Remove a file from a workspace")
async def delete_workspace_file(workspace_id: uuid.UUID, file_id: int):
    _require_neon()
    async with get_session() as s:
        r = await s.execute(
            select(WorkspaceFile).where(
                WorkspaceFile.id == file_id,
                WorkspaceFile.workspace_id == workspace_id,
            )
        )
        wf = r.scalar_one_or_none()
        if wf is None:
            raise HTTPException(status_code=404, detail="File not found.")

        # Best-effort blob + local disk cleanup
        try:
            if wf.blob_url:
                from src.infra.blob_loader import delete_blob
                delete_blob(wf.blob_url)
        except Exception as exc:
            logger.warning("Blob delete failed for file=%s: %s", file_id, exc)

        try:
            if wf.local_path:
                from pathlib import Path as _P
                _P(wf.local_path).unlink(missing_ok=True)
        except Exception:
            pass

        await s.delete(wf)
        await s.commit()
    return {"ok": True, "deleted_file_id": file_id}
