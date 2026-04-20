"""
Build trigger + status endpoints — workspace-scoped.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from src.api import build_jobs
from src.api.auth import require_user
from src.api.deps import require_workspace_id
from src.infra.db import get_session
from src.infra.db_models import BuildJobRow, User, Workspace

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
):
    try:
        job = build_jobs.start_build(
            workspace_id=workspace_id,
            skip_embeddings=req.skip_embeddings,
            skip_llm_cross_links=req.skip_llm_cross_links,
        )
    except RuntimeError as exc:
        running = build_jobs.current_running_job_id(workspace_id)
        raise HTTPException(status_code=409, detail={"error": str(exc), "running_job_id": running})
    return {"job_id": job.job_id, "workspace_id": job.workspace_id, "status": job.status}


@router.get("/build/{job_id}", response_model=BuildJobResponse, summary="Get build status")
async def get_build_status(job_id: str, user: User = Depends(require_user)):
    """Return build status — ownership-checked via the job's workspace_id.

    We look up the BuildJobRow in Neon (authoritative record of
    workspace_id); the in-memory ``build_jobs`` dict is only used for the
    live ``log_tail`` / ``stage`` updates.
    """
    # First, ownership: the job must belong to a workspace this user owns.
    # A non-existent job and a cross-tenant job both return 404 to avoid
    # leaking job existence across accounts.
    async with get_session() as s:
        row = (await s.execute(
            select(BuildJobRow.workspace_id).where(BuildJobRow.job_id == job_id)
        )).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail=f"Build job '{job_id}' not found")
        ws_owner = (await s.execute(
            select(Workspace.user_id).where(Workspace.id == row)
        )).scalar_one_or_none()
        if ws_owner is None or ws_owner != user.id:
            raise HTTPException(status_code=404, detail=f"Build job '{job_id}' not found")

    # Ownership ok — return the live in-memory job snapshot.
    job = build_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Build job '{job_id}' not found")
    return BuildJobResponse(**job.to_dict())


@router.get("/build", summary="Get currently running build for this workspace (if any)")
async def current_build(workspace_id: str = Depends(require_workspace_id)):
    running = build_jobs.current_running_job_id(workspace_id)
    if running is None:
        return {"running": False, "job_id": None}
    job = build_jobs.get_job(running)
    return {"running": True, "job_id": running, "status": job.status if job else None}
