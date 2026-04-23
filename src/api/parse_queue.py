"""Durable queue for vision-OCR parse jobs, backed by Neon.

Sibling of ``src.api.build_queue`` — same SELECT FOR UPDATE SKIP LOCKED
claim pattern, same heartbeat/zombie-sweep semantics — differs only in:

- **No per-workspace single-flight.** A user uploading 10 PDFs at once
  wants all 10 queued. The worker's concurrency cap (``_parse_semaphore``
  in :mod:`src.worker.build_worker`) bounds how many run in parallel.
- **Terminal state includes ``cancelled``** for the future delete-job
  endpoint.
- **Progress columns:** ``percent`` + ``pages_done`` are updated by the
  worker as OCR completes. Callers poll via the routes in
  :mod:`src.api.parse_jobs_routes`.
"""

from __future__ import annotations

import logging
import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, text, update

from src.infra.db import get_session
from src.infra.db_models import ParseJobRow

logger = logging.getLogger(__name__)


HEARTBEAT_TIMEOUT_SECONDS = 90
MAX_ATTEMPTS = 3
CLAIM_POLL_INTERVAL_SECONDS = 2


# ---------------------------------------------------------------------------
# Worker identity
# ---------------------------------------------------------------------------

def make_worker_id() -> str:
    host = socket.gethostname()
    pid = os.getpid()
    rand = uuid.uuid4().hex[:8]
    return f"{host}/{pid}/{rand}"


# ---------------------------------------------------------------------------
# DTO the worker consumes
# ---------------------------------------------------------------------------

@dataclass
class ClaimedParseJob:
    job_id: str
    workspace_id: str
    user_id: str
    kb_source: str
    filename: str
    source_document_url: str
    source_document_sha256: str
    source_document_size: int
    source_mime: str
    attempt_count: int


def _row_to_claimed(row: ParseJobRow) -> ClaimedParseJob:
    return ClaimedParseJob(
        job_id=row.job_id,
        workspace_id=str(row.workspace_id),
        user_id=str(row.user_id),
        kb_source=row.kb_source,
        filename=row.filename,
        source_document_url=row.source_document_url,
        source_document_sha256=row.source_document_sha256,
        source_document_size=int(row.source_document_size),
        source_mime=row.source_mime,
        attempt_count=int(row.attempt_count or 1),
    )


# ---------------------------------------------------------------------------
# API-side: enqueue
# ---------------------------------------------------------------------------

async def enqueue(
    *,
    workspace_id: str,
    user_id: str,
    kb_source: str,
    filename: str,
    source_document_url: str,
    source_document_sha256: str,
    source_document_size: int,
    source_mime: str,
) -> dict:
    """INSERT a new queued parse job. No uniqueness check — concurrent
    parses for the same workspace are legal."""
    job_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)
    async with get_session() as s:
        row = ParseJobRow(
            job_id=job_id,
            workspace_id=uuid.UUID(workspace_id),
            user_id=uuid.UUID(user_id),
            kb_source=kb_source,
            filename=filename,
            source_document_url=source_document_url,
            source_document_sha256=source_document_sha256,
            source_document_size=source_document_size,
            source_mime=source_mime,
            status="queued",
            percent=0,
            pages_total=0,
            pages_done=0,
            started_at=now,
            attempt_count=1,
        )
        s.add(row)
        await s.commit()
    return {"job_id": job_id, "workspace_id": workspace_id, "status": "queued"}


async def get_job_by_id(job_id: str) -> Optional[ParseJobRow]:
    async with get_session() as s:
        r = (await s.execute(
            select(ParseJobRow).where(ParseJobRow.job_id == job_id)
        )).scalar_one_or_none()
        return r


# ---------------------------------------------------------------------------
# Worker-side: claim / heartbeat / complete
# ---------------------------------------------------------------------------

async def claim_next(worker_id: str) -> Optional[ClaimedParseJob]:
    """Atomically claim the oldest queued parse job.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so a second worker on a
    different instance never sees the same candidate row.
    """
    async with get_session() as s:
        result = await s.execute(text("""
            SELECT job_id
            FROM parse_jobs
            WHERE status = 'queued'
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        """))
        row = result.first()
        if row is None:
            await s.rollback()
            return None
        claimed_job_id = row[0]

        now = datetime.now(timezone.utc)
        await s.execute(
            update(ParseJobRow)
            .where(ParseJobRow.job_id == claimed_job_id)
            .values(
                status="running",
                worker_id=worker_id,
                started_at=now,
                heartbeat_at=now,
                percent=0,
                pages_done=0,
                error=None,
            )
        )
        await s.commit()

        fresh = (await s.execute(
            select(ParseJobRow).where(ParseJobRow.job_id == claimed_job_id)
        )).scalar_one_or_none()
        if fresh is None:
            logger.warning("parse claim_next: row %s vanished after claim", claimed_job_id)
            return None
        return _row_to_claimed(fresh)


async def touch_heartbeat(job_id: str) -> None:
    """Liveness-only bump."""
    async with get_session() as s:
        await s.execute(
            update(ParseJobRow)
            .where(ParseJobRow.job_id == job_id)
            .values(heartbeat_at=datetime.now(timezone.utc))
        )
        await s.commit()


async def update_progress(
    job_id: str,
    pages_done: int,
    pages_total: int,
) -> None:
    """Writes pages_done / pages_total / percent + heartbeat in one row update.

    Called from inside the vision pipeline after every page — so we
    keep it a single narrow UPDATE to minimise autovacuum churn.
    """
    percent = int(100 * pages_done / pages_total) if pages_total > 0 else 0
    percent = max(0, min(99, percent))  # terminal 100 only on complete()
    async with get_session() as s:
        await s.execute(
            update(ParseJobRow)
            .where(ParseJobRow.job_id == job_id)
            .values(
                heartbeat_at=datetime.now(timezone.utc),
                pages_done=pages_done,
                pages_total=pages_total,
                percent=percent,
            )
        )
        await s.commit()


async def complete(
    job_id: str,
    *,
    status: str,
    result_file_id: Optional[int] = None,
    tokens_in: Optional[int] = None,
    tokens_out: Optional[int] = None,
    cost_usd: Optional[float] = None,
    error: Optional[str] = None,
    pages_total: Optional[int] = None,
    pages_done: Optional[int] = None,
) -> None:
    assert status in ("done", "error", "cancelled"), f"invalid terminal status: {status}"
    values: dict = {
        "status": status,
        "finished_at": datetime.now(timezone.utc),
        "heartbeat_at": datetime.now(timezone.utc),
    }
    if status == "done":
        values["percent"] = 100
    if result_file_id is not None:
        values["result_file_id"] = result_file_id
    if tokens_in is not None:
        values["tokens_in"] = tokens_in
    if tokens_out is not None:
        values["tokens_out"] = tokens_out
    if cost_usd is not None:
        values["cost_usd"] = cost_usd
    if error is not None:
        values["error"] = error
    if pages_total is not None:
        values["pages_total"] = pages_total
    if pages_done is not None:
        values["pages_done"] = pages_done

    async with get_session() as s:
        await s.execute(
            update(ParseJobRow)
            .where(ParseJobRow.job_id == job_id)
            .values(**values)
        )
        await s.commit()


# ---------------------------------------------------------------------------
# Sweeper — zombie recovery
# ---------------------------------------------------------------------------

async def sweep_stale() -> int:
    """Flip zombie 'running' rows to 'error' (or re-queue if retries left)."""
    timeout_clause = f"now() - interval '{HEARTBEAT_TIMEOUT_SECONDS} seconds'"
    async with get_session() as s:
        result = await s.execute(text(f"""
            UPDATE parse_jobs
            SET
              attempt_count = attempt_count + 1,
              status = CASE
                WHEN attempt_count + 1 < {MAX_ATTEMPTS} THEN 'queued'
                ELSE 'error'
              END,
              error = CASE
                WHEN attempt_count + 1 < {MAX_ATTEMPTS} THEN NULL
                ELSE 'heartbeat lost — worker crashed and max attempts exhausted'
              END,
              finished_at = CASE
                WHEN attempt_count + 1 < {MAX_ATTEMPTS} THEN NULL
                ELSE now()
              END,
              worker_id = NULL,
              heartbeat_at = NULL
            WHERE status = 'running'
              AND heartbeat_at IS NOT NULL
              AND heartbeat_at < {timeout_clause}
            RETURNING job_id, attempt_count, status
        """))
        swept = result.fetchall()
        await s.commit()
        for job_id, attempt, new_status in swept:
            logger.warning(
                "parse sweep_stale: job=%s attempt=%d -> %s",
                job_id, attempt, new_status,
            )
        return len(swept)
