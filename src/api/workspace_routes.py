"""
Workspace CRUD: list / create / detail / delete.

Every route requires the caller's Stack Auth identity (soft-fallbacks to a
shared dev user when Stack env vars are unset) and enforces ownership — a
caller can only touch workspaces where ``workspace.user_id == user.id``.

Cross-tenant access returns 404 (not 403) to avoid leaking existence.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.api.auth import require_user
from src.config import USE_MEMGRAPH, USE_NEON, USE_QDRANT
from src.infra.audit import record_audit
from src.infra.db import get_session
from src.infra.db_models import (
    BuildJobRow,
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

async def _summary_for(workspace: Workspace, session) -> dict:
    counts_res = await session.execute(
        select(WorkspaceFile.kb_source, func.count(WorkspaceFile.id))
        .where(WorkspaceFile.workspace_id == workspace.id)
        .where(WorkspaceFile.active.is_(True))
        .group_by(WorkspaceFile.kb_source)
    )
    file_counts = {"knowledge": 0, "tool": 0}
    for kb_src, cnt in counts_res.all():
        file_counts[kb_src] = int(cnt)

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


def _require_neon() -> None:
    if not USE_NEON:
        raise HTTPException(status_code=503, detail="Workspaces require DATABASE_URL (Neon).")


async def _load_owned_workspace(session, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Workspace:
    """Load a workspace row, raising 404 if it doesn't exist OR isn't owned.

    Returns 404 (not 403) on ownership mismatch so tenant existence isn't
    leaked across accounts.
    """
    ws = (await session.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    )).scalar_one_or_none()
    if ws is None or ws.user_id != user_id:
        raise HTTPException(status_code=404, detail="Workspace not found.")
    return ws


# ---------------------------------------------------------------------------
# Routes — CRUD
# ---------------------------------------------------------------------------

@router.get("/workspaces", summary="List workspaces owned by the current user")
async def list_workspaces(user: User = Depends(require_user)):
    _require_neon()
    async with get_session() as s:
        result = await s.execute(
            select(Workspace)
            .where(Workspace.user_id == user.id)
            .order_by(Workspace.updated_at.desc())
        )
        workspaces = result.scalars().all()
        summaries = [await _summary_for(w, s) for w in workspaces]
    return {"workspaces": summaries}


@router.post("/workspaces", summary="Create a new workspace", status_code=201)
async def create_workspace(body: WorkspaceCreate, user: User = Depends(require_user)):
    _require_neon()
    async with get_session() as s:
        existing = await s.execute(
            select(Workspace).where(Workspace.user_id == user.id, Workspace.name == body.name)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail=f"Workspace named {body.name!r} already exists.")

        ws = Workspace(user_id=user.id, name=body.name, description=body.description)
        s.add(ws)
        await s.commit()
        await s.refresh(ws)
        summary = await _summary_for(ws, s)

    record_audit(
        user.id, "workspace.create",
        target_type="workspace", target_id=str(ws.id),
        workspace_id=ws.id,
        metadata={"name": ws.name},
    )
    return summary


@router.get("/workspaces/{workspace_id}", summary="Get workspace detail")
async def get_workspace(workspace_id: uuid.UUID, user: User = Depends(require_user)):
    _require_neon()
    async with get_session() as s:
        ws = await _load_owned_workspace(s, workspace_id, user.id)
        return await _summary_for(ws, s)


@router.patch("/workspaces/{workspace_id}", summary="Rename / update a workspace")
async def update_workspace(
    workspace_id: uuid.UUID,
    body: WorkspaceUpdate,
    user: User = Depends(require_user),
):
    _require_neon()
    changed_fields: list[str] = []
    async with get_session() as s:
        ws = await _load_owned_workspace(s, workspace_id, user.id)
        if body.name is not None and body.name != ws.name:
            ws.name = body.name
            changed_fields.append("name")
        if body.description is not None and body.description != ws.description:
            ws.description = body.description
            changed_fields.append("description")
        ws.updated_at = datetime.now(timezone.utc)
        await s.commit()
        await s.refresh(ws)
        summary = await _summary_for(ws, s)

    if changed_fields:
        record_audit(
            user.id, "workspace.update",
            target_type="workspace", target_id=str(workspace_id),
            workspace_id=workspace_id,
            metadata={"fields": changed_fields},
        )
    return summary


async def delete_workspace_cascade(workspace_id: uuid.UUID) -> None:
    """Shared cleanup: purge Memgraph, Qdrant, Vercel Blob, then delete the
    Neon row (cascading through FKs).

    Exposed as a plain async function (not a FastAPI route) so the anon-
    migration script can reuse it without needing to synthesize a fake user.
    Best-effort on the side stores — a dead store never blocks the Neon delete.
    """
    wid_str = str(workspace_id)

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

    try:
        from src.infra.blob_loader import delete_workspace_blobs
        delete_workspace_blobs(wid_str)
    except Exception as exc:
        logger.warning("Blob cleanup for ws=%s failed: %s", wid_str, exc)

    async with get_session() as s:
        ws = (await s.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )).scalar_one_or_none()
        if ws is not None:
            await s.delete(ws)
            await s.commit()


@router.delete("/workspaces/{workspace_id}", summary="Delete a workspace and all its artifacts")
async def delete_workspace(workspace_id: uuid.UUID, user: User = Depends(require_user)):
    """Ownership-checked HTTP wrapper around ``delete_workspace_cascade``."""
    _require_neon()
    async with get_session() as s:
        ws = await _load_owned_workspace(s, workspace_id, user.id)
        name = ws.name
    # Record the audit event BEFORE cascade so the target_id is still valid
    # in the workspace_id FK column (SET NULL triggers when the row goes away).
    record_audit(
        user.id, "workspace.delete",
        target_type="workspace", target_id=str(workspace_id),
        workspace_id=workspace_id,
        metadata={"name": name},
    )
    await delete_workspace_cascade(workspace_id)
    return {"ok": True, "deleted_workspace_id": str(workspace_id)}


@router.get("/workspaces/{workspace_id}/files", summary="List files in a workspace")
async def list_workspace_files(
    workspace_id: uuid.UUID,
    kb_source: Optional[str] = None,
    user: User = Depends(require_user),
):
    _require_neon()
    async with get_session() as s:
        await _load_owned_workspace(s, workspace_id, user.id)
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
                "active": r.active,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.delete("/workspaces/{workspace_id}/files/{file_id}", summary="Remove a file from a workspace")
async def delete_workspace_file(
    workspace_id: uuid.UUID,
    file_id: int,
    user: User = Depends(require_user),
):
    _require_neon()
    async with get_session() as s:
        await _load_owned_workspace(s, workspace_id, user.id)
        wf = (await s.execute(
            select(WorkspaceFile).where(
                WorkspaceFile.id == file_id,
                WorkspaceFile.workspace_id == workspace_id,
            )
        )).scalar_one_or_none()
        if wf is None:
            raise HTTPException(status_code=404, detail="File not found.")

        try:
            if wf.blob_url:
                from src.infra.blob_loader import delete_blob
                delete_blob(wf.blob_url)
        except Exception as exc:
            logger.warning("Blob delete failed for file=%s: %s", file_id, exc)

        await s.delete(wf)
        await s.commit()

    record_audit(
        user.id, "file.delete",
        target_type="file", target_id=str(file_id),
        workspace_id=workspace_id,
    )
    return {"ok": True, "deleted_file_id": file_id}
