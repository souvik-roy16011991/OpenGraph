"""Progress + status endpoint for the vision-OCR parse queue.

    GET /api/v1/kb/parse-jobs/{job_id}

Ownership is enforced by comparing the requesting user against the
``user_id`` stored on the ParseJob row (denormalised from the owning
workspace). A mismatch returns 404 (not 403) to avoid leaking the
existence of jobs belonging to other users.

The frontend's ``ParseProgressCard`` polls this endpoint every ~3s until
``status`` reaches a terminal value (done / error / cancelled). On
``done`` the card invalidates the workspace-files query and disappears;
the real WorkspaceFile row the worker wrote is what the user sees.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from src.api.auth import require_user
from src.api.parse_queue import get_job_by_id, list_jobs_for_user
from src.infra.db_models import User

logger = logging.getLogger(__name__)

router = APIRouter()


class ParseJobStatus(BaseModel):
    job_id: str
    workspace_id: str
    kb_source: str
    filename: str
    status: str            # queued | running | done | error | cancelled
    percent: int
    pages_total: int
    pages_done: int
    error: Optional[str] = None
    result_file_id: Optional[int] = None
    source_document_url: str
    tokens_in: int
    tokens_out: int
    cost_usd: Optional[float] = None
    created_at: str
    finished_at: Optional[str] = None


class ParseJobsListResponse(BaseModel):
    jobs: list[ParseJobStatus]
    # Convenience rollups — lets the UI render "147/200 done, 45 running"
    # without re-scanning the jobs array on every render tick.
    total: int
    by_status: dict[str, int]


# Parse-job statuses the client can filter on. Kept as a tuple so we
# can validate incoming strings without taking an Enum dependency.
_VALID_STATUS = ("queued", "running", "done", "error", "cancelled")


@router.get(
    "/kb/parse-jobs",
    response_model=ParseJobsListResponse,
    summary="List vision-OCR parse jobs for the current user (batched progress poll)",
)
async def list_parse_jobs(
    user: User = Depends(require_user),
    workspace_id: Optional[str] = Query(
        default=None, description="Scope to one workspace. Omit to list across all the caller's workspaces."
    ),
    status: Optional[str] = Query(
        default=None,
        description=(
            "Comma-separated list of statuses to filter on "
            "(queued, running, done, error, cancelled). Default: active only "
            "(queued + running) — the common case for progress UIs."
        ),
    ),
    limit: int = Query(default=200, ge=1, le=500),
) -> ParseJobsListResponse:
    """Returns every parse job the caller can see, in one query.

    The UI's per-job polling scales O(N) in request volume; this
    endpoint scales O(1). At 200 concurrent parses the difference is
    67 req/s vs. 1 req/3s.
    """
    if status:
        wanted = [s.strip() for s in status.split(",") if s.strip()]
        bad = [s for s in wanted if s not in _VALID_STATUS]
        if bad:
            raise HTTPException(
                status_code=400,
                detail=f"unknown status filter(s): {bad}. valid: {list(_VALID_STATUS)}",
            )
    else:
        wanted = ["queued", "running"]

    rows = await list_jobs_for_user(
        user_id=str(user.id),
        workspace_id=workspace_id,
        statuses=wanted,
        limit=limit,
    )

    jobs = [
        ParseJobStatus(
            job_id=r.job_id,
            workspace_id=str(r.workspace_id),
            kb_source=r.kb_source,
            filename=r.filename,
            status=r.status,
            percent=int(r.percent or 0),
            pages_total=int(r.pages_total or 0),
            pages_done=int(r.pages_done or 0),
            error=r.error,
            result_file_id=r.result_file_id,
            source_document_url=r.source_document_url,
            tokens_in=int(r.tokens_in or 0),
            tokens_out=int(r.tokens_out or 0),
            cost_usd=r.cost_usd,
            created_at=r.created_at.isoformat() if r.created_at else "",
            finished_at=r.finished_at.isoformat() if r.finished_at else None,
        )
        for r in rows
    ]

    by_status: dict[str, int] = {}
    for j in jobs:
        by_status[j.status] = by_status.get(j.status, 0) + 1

    return ParseJobsListResponse(jobs=jobs, total=len(jobs), by_status=by_status)


@router.get(
    "/kb/parse-jobs/{job_id}",
    response_model=ParseJobStatus,
    summary="Poll a vision-OCR parse job",
)
async def get_parse_job(
    job_id: str,
    user: User = Depends(require_user),
) -> ParseJobStatus:
    row = await get_job_by_id(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="parse job not found")

    # Ownership check — the job's denormalised user_id is authoritative.
    # Using 404 instead of 403 avoids confirming existence to an outsider.
    if row.user_id != user.id:
        raise HTTPException(status_code=404, detail="parse job not found")

    return ParseJobStatus(
        job_id=row.job_id,
        workspace_id=str(row.workspace_id),
        kb_source=row.kb_source,
        filename=row.filename,
        status=row.status,
        percent=int(row.percent or 0),
        pages_total=int(row.pages_total or 0),
        pages_done=int(row.pages_done or 0),
        error=row.error,
        result_file_id=row.result_file_id,
        source_document_url=row.source_document_url,
        tokens_in=int(row.tokens_in or 0),
        tokens_out=int(row.tokens_out or 0),
        cost_usd=row.cost_usd,
        created_at=row.created_at.isoformat() if row.created_at else "",
        finished_at=row.finished_at.isoformat() if row.finished_at else None,
    )
