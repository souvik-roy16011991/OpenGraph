"""
Build trigger + status endpoints — workspace-scoped.

POST enqueues a row into Neon; worker processes claim it via
``SELECT ... FOR UPDATE SKIP LOCKED``. GET reads state back from Neon
(the API holds no in-memory job state). Supports ``If-Modified-Since``
to let the frontend poll cheaply — unchanged rows return 304.
"""

from __future__ import annotations

import email.utils
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select

from src.api import build_queue
from src.api.auth import require_user
from src.api.deps import require_workspace_id
from src.billing import check_build_allowed
from src.infra.audit import record_audit
from src.infra.db import get_session
from src.infra.db_models import BuildJobRow, User, Workspace

logger = logging.getLogger(__name__)
router = APIRouter()


class BuildRequest(BaseModel):
    skip_embeddings: bool = False
    skip_llm_cross_links: bool = False


class BuildJobResponse(BaseModel):
    job_id: str
    workspace_id: str
    status: str
    stage: int
    stage_name: str
    percent: int
    started_at: float
    finished_at: float | None = None
    error: str | None = None
    log_tail: list[str]
    skip_embeddings: bool
    skip_llm_cross_links: bool


@router.post("/build", summary="Start a workspace-scoped background build")
async def start_build(
    req: BuildRequest,
    workspace_id: str = Depends(require_workspace_id),
    user: User = Depends(require_user),
):
    # Billing gate — raises HTTP 402 with structured detail on trial-cap
    # hit or PAYG/Team overdraft.
    await check_build_allowed(user.id)

    try:
        created = await build_queue.enqueue(
            workspace_id=workspace_id,
            skip_embeddings=req.skip_embeddings,
            skip_llm_cross_links=req.skip_llm_cross_links,
        )
    except build_queue.BuildAlreadyRunning as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": str(exc),
                "running_job_id": exc.existing_job_id,
            },
        )

    record_audit(
        user.id, "build.start",
        target_type="build_job", target_id=created["job_id"],
        workspace_id=workspace_id,
        metadata={
            "skip_embeddings": req.skip_embeddings,
            "skip_llm_cross_links": req.skip_llm_cross_links,
        },
    )
    return created


@router.get("/build/{job_id}", response_model=BuildJobResponse, summary="Get build status")
async def get_build_status(
    job_id: str,
    response: Response,
    user: User = Depends(require_user),
    if_modified_since: str | None = Header(default=None, alias="If-Modified-Since"),
):
    """Return the build's current status. Honours ``If-Modified-Since``
    against ``heartbeat_at`` — returns 304 when unchanged to keep polling
    cheap at scale."""
    row = await build_queue.get_job_by_id(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Build job '{job_id}' not found")

    # Ownership: 404 both for nonexistent and cross-tenant — no existence leak.
    async with get_session() as s:
        owner = (await s.execute(
            select(Workspace.user_id).where(Workspace.id == row.workspace_id)
        )).scalar_one_or_none()
    if owner is None or owner != user.id:
        raise HTTPException(status_code=404, detail=f"Build job '{job_id}' not found")

    # If-Modified-Since short-circuit. The live "modified" timestamp is
    # ``heartbeat_at`` while running, ``finished_at`` once terminal.
    live_ts = row.heartbeat_at or row.finished_at or row.started_at
    if live_ts and if_modified_since:
        try:
            client_ts = email.utils.parsedate_to_datetime(if_modified_since)
            # Compare at 1-second resolution (HTTP date precision).
            if client_ts is not None and int(client_ts.timestamp()) >= int(live_ts.timestamp()):
                response.status_code = 304
                return Response(status_code=304)
        except Exception:
            pass

    # Tag the response with Last-Modified so the client can send it back.
    if live_ts:
        response.headers["Last-Modified"] = email.utils.format_datetime(live_ts)

    return BuildJobResponse(
        job_id=row.job_id,
        workspace_id=str(row.workspace_id),
        status=row.status,
        stage=row.stage,
        stage_name=row.stage_name,
        percent=row.percent,
        started_at=row.started_at.timestamp() if row.started_at else 0.0,
        finished_at=row.finished_at.timestamp() if row.finished_at else None,
        error=row.error,
        log_tail=list(row.log_tail or []),
        skip_embeddings=bool(row.skip_embeddings),
        skip_llm_cross_links=bool(row.skip_llm_cross_links),
    )


@router.get("/build", summary="Get currently running build for this workspace (if any)")
async def current_build(workspace_id: str = Depends(require_workspace_id)):
    running = await build_queue.current_running_for_workspace(workspace_id)
    if running is None:
        return {"running": False, "job_id": None}
    row = await build_queue.get_job_by_id(running)
    return {
        "running": True,
        "job_id": running,
        "status": row.status if row else None,
    }
