"""
Workspace CRUD: list / create / detail / delete.

Every route resolves the caller via ``require_user`` (Supabase JWT) and
enforces ownership — a caller can only touch workspaces where
``workspace.user_id == user.id``.

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
from sqlalchemy.exc import IntegrityError

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
    """Summary for a *single* workspace — two round-trips.

    Used by detail / create / update endpoints that only ever touch one row.
    The list endpoint uses the batched ``_summaries_for_many`` instead to
    collapse N+1 into three queries total.
    """
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


async def _summaries_for_many(workspaces: list[Workspace], session) -> list[dict]:
    """Batched summary for a list of workspaces — 3 queries, not 2N.

    Collapses the list endpoint's N+1 pattern. Expected shape matches
    ``_summary_for`` exactly so the response stays API-compatible.
    """
    if not workspaces:
        return []

    ws_ids = [w.id for w in workspaces]

    # 1) File counts by (workspace_id, kb_source) across all workspaces.
    counts_res = await session.execute(
        select(
            WorkspaceFile.workspace_id,
            WorkspaceFile.kb_source,
            func.count(WorkspaceFile.id),
        )
        .where(WorkspaceFile.workspace_id.in_(ws_ids))
        .where(WorkspaceFile.active.is_(True))
        .group_by(WorkspaceFile.workspace_id, WorkspaceFile.kb_source)
    )
    counts: dict[uuid.UUID, dict[str, int]] = {}
    for wid, kb_src, cnt in counts_res.all():
        counts.setdefault(wid, {"knowledge": 0, "tool": 0})[kb_src] = int(cnt)

    # 2) Latest build per workspace via Postgres DISTINCT ON.
    # ``DISTINCT ON (workspace_id)`` + ``ORDER BY workspace_id, created_at DESC``
    # yields the newest row per workspace in one pass.
    last_res = await session.execute(
        select(BuildJobRow)
        .where(BuildJobRow.workspace_id.in_(ws_ids))
        .order_by(BuildJobRow.workspace_id, BuildJobRow.created_at.desc())
        .distinct(BuildJobRow.workspace_id)
    )
    latest: dict[uuid.UUID, BuildJobRow] = {r.workspace_id: r for r in last_res.scalars().all()}

    out: list[dict] = []
    for w in workspaces:
        fc = counts.get(w.id, {"knowledge": 0, "tool": 0})
        last = latest.get(w.id)
        out.append({
            "id": str(w.id),
            "name": w.name,
            "description": w.description,
            "created_at": w.created_at.isoformat(),
            "updated_at": w.updated_at.isoformat(),
            "file_counts": {"knowledge": fc.get("knowledge", 0), "tool": fc.get("tool", 0)},
            "last_build_at": last.created_at.isoformat() if last else None,
            "last_build_status": last.status if last else None,
            "stats": last.stats if last else None,
        })
    return out


def _require_neon() -> None:
    if not USE_NEON:
        raise HTTPException(status_code=503, detail="Workspaces require DATABASE_URL (Neon).")


async def _load_owned_workspace(session, workspace_id: uuid.UUID, user_id: uuid.UUID) -> Workspace:
    """Load a workspace row, raising 404 if it doesn't exist OR isn't owned.

    Returns 404 (not 403) on ownership mismatch so tenant existence isn't
    leaked across accounts. Soft-deleted rows (``deleted_at IS NOT NULL``)
    are treated as not-found: the HTTP DELETE handler marks a workspace
    deleted synchronously, and the user should stop seeing it the moment
    that request returns even while the saga sweeper is still cleaning
    side stores.
    """
    ws = (await session.execute(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.deleted_at.is_(None),
        )
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
            .where(
                Workspace.user_id == user.id,
                Workspace.deleted_at.is_(None),
            )
            .order_by(Workspace.updated_at.desc())
        )
        workspaces = result.scalars().all()
        summaries = await _summaries_for_many(list(workspaces), s)
    return {"workspaces": summaries}


@router.post("/workspaces", summary="Create a new workspace", status_code=201)
async def create_workspace(body: WorkspaceCreate, user: User = Depends(require_user)):
    _require_neon()

    # Billing gate — per-tier workspace cap. Trial=1, PAYG=25, Team=50.
    # Raises HTTP 402 when the user already owns the maximum they're
    # allowed on their current tier.
    from src.billing import check_workspace_create_allowed
    await check_workspace_create_allowed(user.id)

    async with get_session() as s:
        existing = await s.execute(
            select(Workspace).where(Workspace.user_id == user.id, Workspace.name == body.name)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail=f"Workspace named {body.name!r} already exists.")

        ws = Workspace(user_id=user.id, name=body.name, description=body.description)
        s.add(ws)
        try:
            await s.commit()
        except IntegrityError:
            # Concurrent create with the same (user_id, name) — two requests
            # both passed the SELECT check above; the DB unique constraint
            # just rejected the second. Surface as 409 instead of 500.
            await s.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"Workspace named {body.name!r} already exists.",
            )
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


async def _try_side_store_cleanup(workspace_id: uuid.UUID) -> Optional[str]:
    """Run the three side-store purges (Memgraph, Qdrant, Blob).

    Returns None on full success, or a human-readable error string on the
    first failure. Each call is idempotent so repeated invocations by the
    sweeper are safe: clear_workspace issues a DELETE on workspace-scoped
    nodes, delete_collection 404 is swallowed, Blob prefix wipe is a
    best-effort list-and-delete.
    """
    wid_str = str(workspace_id)

    if USE_MEMGRAPH:
        try:
            from src.config import MEMGRAPH_DATABASE, MEMGRAPH_PASSWORD, MEMGRAPH_URI, MEMGRAPH_USERNAME
            from src.infra.memgraph_store import MemgraphGraphStore
            store = MemgraphGraphStore(MEMGRAPH_URI, MEMGRAPH_USERNAME, MEMGRAPH_PASSWORD, MEMGRAPH_DATABASE)
            store.clear_workspace(wid_str)
        except Exception as exc:
            return f"Memgraph: {exc}"

    if USE_QDRANT:
        try:
            from src.config import QDRANT_API_KEY, QDRANT_SHARED_COLLECTION, QDRANT_URL
            from qdrant_client import QdrantClient
            from src.graph_builder.builder import _short_wid
            client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30)
            if QDRANT_SHARED_COLLECTION:
                # Shared-collection mode: only delete this tenant's points;
                # leave everyone else alone.
                try:
                    from qdrant_client.models import (
                        FieldCondition,
                        Filter,
                        FilterSelector,
                        MatchValue,
                    )
                    flt = Filter(
                        must=[FieldCondition(
                            key="workspace_id", match=MatchValue(value=wid_str)
                        )]
                    )
                    client.delete(
                        collection_name=QDRANT_SHARED_COLLECTION,
                        points_selector=FilterSelector(filter=flt),
                        wait=True,
                    )
                except Exception as exc:
                    msg = str(exc).lower()
                    if "not found" not in msg and "doesn't exist" not in msg:
                        return f"Qdrant filter-delete: {exc}"
            else:
                collection = f"kb-{_short_wid(wid_str)}"
                try:
                    client.delete_collection(collection)
                except Exception as exc:
                    # 404 on collection is fine — means an earlier attempt
                    # already succeeded or the workspace was never built.
                    # Anything else is a real failure the sweeper retries.
                    msg = str(exc).lower()
                    if "not found" not in msg and "doesn't exist" not in msg:
                        return f"Qdrant delete_collection: {exc}"
        except Exception as exc:
            return f"Qdrant client: {exc}"

    try:
        from src.infra.blob_loader import delete_workspace_blobs
        delete_workspace_blobs(wid_str)
    except Exception as exc:
        return f"Blob: {exc}"

    return None


async def delete_workspace_cascade(workspace_id: uuid.UUID) -> bool:
    """Soft-delete a workspace and attempt the side-store cascade.

    Flow:
      1. Set ``deleted_at = now()`` (workspace disappears from all read paths).
      2. Attempt Memgraph / Qdrant / Blob cleanup.
      3. On full success, hard-delete the Neon row (FKs cascade).
      4. On any cleanup failure, keep the soft-deleted row with the error
         recorded; ``sweep_pending_deletions`` will retry until success.

    Returns True when the row was hard-deleted. False means the saga is
    still pending and the sweeper will pick it up later.
    """
    from datetime import datetime, timezone

    async with get_session() as s:
        ws = (await s.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )).scalar_one_or_none()
        if ws is None:
            return True  # already gone — idempotent
        if ws.deleted_at is None:
            ws.deleted_at = datetime.now(timezone.utc)
            await s.commit()

    error = await _try_side_store_cleanup(workspace_id)

    async with get_session() as s:
        ws = (await s.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )).scalar_one_or_none()
        if ws is None:
            return True
        if error is None:
            await s.delete(ws)
            await s.commit()
            return True
        ws.deletion_failure_count = (ws.deletion_failure_count or 0) + 1
        ws.deletion_last_error = error[:4096]
        await s.commit()
    logger.warning("Workspace %s cascade failed (attempt %d): %s",
                   workspace_id, (ws.deletion_failure_count or 0), error)
    return False


async def sweep_pending_deletions(max_attempts: int = 20) -> int:
    """Retry workspace deletions whose side-store cascade previously failed.

    Picks up any row with ``deleted_at IS NOT NULL`` and retries the
    side-store cleanup. Hard-deletes rows that now succeed. Leaves rows
    that have exceeded ``max_attempts`` as a permanent "tombstone" for
    operator inspection (they still don't appear in the user's UI — the
    ``deleted_at`` filter hides them — but the side-store artifacts need
    manual review).

    Returns the number of rows hard-deleted on this sweep.
    """
    from datetime import datetime, timedelta, timezone

    if not USE_NEON:
        return 0
    # Only pick rows whose ``deleted_at`` is at least ~1 minute old, so the
    # inline cascade from ``delete_workspace_cascade`` gets first shot and
    # the sweeper isn't racing the request handler on the common path.
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)

    async with get_session() as s:
        rows = (await s.execute(
            select(Workspace)
            .where(Workspace.deleted_at.isnot(None))
            .where(Workspace.deleted_at < cutoff)
            .where(Workspace.deletion_failure_count < max_attempts)
            .limit(50)
        )).scalars().all()

    cleared = 0
    for ws in rows:
        error = await _try_side_store_cleanup(ws.id)
        async with get_session() as s:
            live = (await s.execute(
                select(Workspace).where(Workspace.id == ws.id)
            )).scalar_one_or_none()
            if live is None:
                cleared += 1
                continue
            if error is None:
                await s.delete(live)
                await s.commit()
                cleared += 1
            else:
                live.deletion_failure_count = (live.deletion_failure_count or 0) + 1
                live.deletion_last_error = error[:4096]
                await s.commit()
    if cleared:
        logger.info("sweep_pending_deletions: cleared %d workspace(s)", cleared)
    return cleared


@router.delete("/workspaces/{workspace_id}", summary="Delete a workspace and all its artifacts")
async def delete_workspace(workspace_id: uuid.UUID, user: User = Depends(require_user)):
    """Ownership-checked HTTP wrapper around ``delete_workspace_cascade``.

    Returns 200 with ``pending: true`` if the side-store cleanup didn't
    complete in the request; the sweeper finishes the job asynchronously.
    The workspace is already hidden from read paths regardless — the user
    sees it as gone immediately.
    """
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
    done = await delete_workspace_cascade(workspace_id)
    return {
        "ok": True,
        "deleted_workspace_id": str(workspace_id),
        "pending": not done,
    }


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
