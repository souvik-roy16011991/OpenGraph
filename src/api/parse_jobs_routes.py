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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.api.auth import require_user
from src.api.parse_queue import get_job_by_id
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
