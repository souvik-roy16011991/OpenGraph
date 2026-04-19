"""
Build trigger + status endpoints. Delegates to src.api.build_jobs.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.api import build_jobs

router = APIRouter()


class BuildRequest(BaseModel):
    skip_embeddings: bool = False
    skip_llm_cross_links: bool = False


class BuildJobResponse(BaseModel):
    job_id: str
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


@router.post("/build", summary="Start a background graph build")
async def start_build(req: BuildRequest):
    try:
        job = build_jobs.start_build(
            skip_embeddings=req.skip_embeddings,
            skip_llm_cross_links=req.skip_llm_cross_links,
        )
    except RuntimeError as exc:
        running = build_jobs.current_running_job_id()
        raise HTTPException(status_code=409, detail={"error": str(exc), "running_job_id": running})
    return {"job_id": job.job_id, "status": job.status}


@router.get("/build/{job_id}", response_model=BuildJobResponse, summary="Get build status")
async def get_build_status(job_id: str):
    job = build_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Build job '{job_id}' not found")
    return BuildJobResponse(**job.to_dict())


@router.get("/build", summary="Get currently running build (if any)")
async def current_build():
    running = build_jobs.current_running_job_id()
    if running is None:
        return {"running": False, "job_id": None}
    job = build_jobs.get_job(running)
    return {"running": True, "job_id": running, "status": job.status if job else None}
