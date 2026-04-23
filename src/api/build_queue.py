"""
Durable build-job queue backed by Neon Postgres.

Replaces the in-memory ``_jobs`` / ``_running_by_ws`` / ``threading.Lock``
triplet that used to live in ``src.api.build_jobs``. Every stateful operation
is now a DB transaction, which means:

- Builds survive worker crashes (row persists; sweeper re-queues stale rows)
- Multiple worker processes coexist (``SELECT ... FOR UPDATE SKIP LOCKED``)
- Multiple API instances coexist (enqueue is a single INSERT, read is a
  single SELECT)
- Per-workspace single-flight is a unique partial index, not a lock

Invariants
----------

1. At most one row per workspace has ``status IN ('queued', 'running')``
   at any time. Enforced by ``uq_build_jobs_active_per_ws`` in
   ``src.infra.db.init_db``. Violating INSERTs raise ``IntegrityError``
   which this module surfaces as :class:`BuildAlreadyRunning`.
2. A ``running`` row is stale if its ``heartbeat_at`` is older than
   :data:`HEARTBEAT_TIMEOUT_SECONDS`. The sweeper flips it to ``error``
   and re-queues once if ``attempt_count < MAX_ATTEMPTS``.
3. Only the worker that claimed a row writes to it. The API never mutates
   ``status`` / ``stats`` / ``log_tail`` — it only reads.
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
from sqlalchemy.exc import IntegrityError

from src.infra.db import get_session
from src.infra.db_models import BuildJobRow

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

HEARTBEAT_TIMEOUT_SECONDS = 90   # after this, sweeper treats a running row as crashed
MAX_ATTEMPTS = 3                  # after N attempts, stay 'error' permanently
CLAIM_POLL_INTERVAL_SECONDS = 2  # worker idle sleep


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class BuildAlreadyRunning(Exception):
    """Raised when an enqueue would violate the per-workspace single-flight
    unique partial index. The caller should surface this as 409 Conflict."""

    def __init__(self, workspace_id: str, existing_job_id: str | None = None):
        self.workspace_id = workspace_id
        self.existing_job_id = existing_job_id
        msg = f"A build is already queued or running for workspace {workspace_id}"
        if existing_job_id:
            msg += f" (job_id={existing_job_id})"
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Worker identity
# ---------------------------------------------------------------------------

def make_worker_id() -> str:
    """Build a human-debuggable worker id: ``<host>/<pid>/<rand>``.

    Persisted on the claimed row so a stuck job can be traced back to the
    process that holds it. Stable for the lifetime of the process.
    """
    host = socket.gethostname()
    pid = os.getpid()
    rand = uuid.uuid4().hex[:8]
    return f"{host}/{pid}/{rand}"


# ---------------------------------------------------------------------------
# Data transfer — a frozen view the worker consumes
# ---------------------------------------------------------------------------

@dataclass
class ClaimedJob:
    """Snapshot of a build row after a successful claim. The worker owns
    this — it must not mutate the live ORM row (sessions are closed by the
    time the worker runs the build in ``asyncio.to_thread``)."""
    job_id: str
    workspace_id: str
    skip_embeddings: bool
    skip_llm_cross_links: bool
    attempt_count: int


def _row_to_claimed(row: BuildJobRow) -> ClaimedJob:
    return ClaimedJob(
        job_id=row.job_id,
        workspace_id=str(row.workspace_id),
        skip_embeddings=bool(row.skip_embeddings),
        skip_llm_cross_links=bool(row.skip_llm_cross_links),
        attempt_count=int(row.attempt_count or 1),
    )


# ---------------------------------------------------------------------------
# API-side operations
# ---------------------------------------------------------------------------

async def enqueue(
    workspace_id: str,
    skip_embeddings: bool = False,
    skip_llm_cross_links: bool = False,
) -> dict:
    """INSERT a new queued build for *workspace_id*.

    Raises :class:`BuildAlreadyRunning` if another row for this workspace is
    already queued or running (unique partial index violation).
    """
    job_id = uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc)

    async with get_session() as s:
        try:
            row = BuildJobRow(
                job_id=job_id,
                workspace_id=uuid.UUID(workspace_id),
                status="queued",
                stage=0,
                stage_name="Queued",
                percent=0,
                started_at=now,
                log_tail=[],
                skip_embeddings=skip_embeddings,
                skip_llm_cross_links=skip_llm_cross_links,
                attempt_count=1,
            )
            s.add(row)
            await s.commit()
        except IntegrityError:
            await s.rollback()
            # Find the existing row so we can tell the caller which job to poll.
            existing = await _find_active_for_workspace(workspace_id)
            raise BuildAlreadyRunning(workspace_id, existing)

    return {"job_id": job_id, "workspace_id": workspace_id, "status": "queued"}


async def _find_active_for_workspace(workspace_id: str) -> Optional[str]:
    async with get_session() as s:
        r = (await s.execute(
            select(BuildJobRow.job_id)
            .where(BuildJobRow.workspace_id == uuid.UUID(workspace_id))
            .where(BuildJobRow.status.in_(["queued", "running"]))
            .limit(1)
        )).scalar_one_or_none()
        return r


async def current_running_for_workspace(workspace_id: str) -> Optional[str]:
    """Return the job_id of the workspace's running build, if any."""
    async with get_session() as s:
        r = (await s.execute(
            select(BuildJobRow.job_id)
            .where(BuildJobRow.workspace_id == uuid.UUID(workspace_id))
            .where(BuildJobRow.status == "running")
            .limit(1)
        )).scalar_one_or_none()
        return r


async def get_job_by_id(job_id: str) -> Optional[BuildJobRow]:
    async with get_session() as s:
        r = (await s.execute(
            select(BuildJobRow).where(BuildJobRow.job_id == job_id)
        )).scalar_one_or_none()
        return r


# ---------------------------------------------------------------------------
# Worker-side operations
# ---------------------------------------------------------------------------

async def claim_next(worker_id: str) -> Optional[ClaimedJob]:
    """Atomically claim the oldest queued job, bumping it to ``running``.

    Uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so concurrent workers on
    different instances never see the same candidate. The UPDATE in the
    same transaction sets ``status='running'``, stamps ``worker_id`` and
    ``heartbeat_at``, then commits. If nothing was queued, returns None.
    """
    async with get_session() as s:
        # SKIP LOCKED is required to prevent a second worker from blocking
        # on the row we're about to update. We use raw text for SKIP LOCKED
        # since SQLAlchemy's with_for_update(skip_locked=True) is
        # driver-dependent; this is clearer.
        result = await s.execute(text("""
            SELECT job_id
            FROM build_jobs
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
            update(BuildJobRow)
            .where(BuildJobRow.job_id == claimed_job_id)
            .values(
                status="running",
                worker_id=worker_id,
                started_at=now,
                heartbeat_at=now,
                stage=0,
                percent=0,
                error=None,
            )
        )
        await s.commit()

        # Re-read the now-running row so we hand the worker a consistent view.
        fresh = (await s.execute(
            select(BuildJobRow).where(BuildJobRow.job_id == claimed_job_id)
        )).scalar_one_or_none()
        if fresh is None:
            logger.warning("claim_next: row %s vanished after claim", claimed_job_id)
            return None
        return _row_to_claimed(fresh)


async def heartbeat(
    job_id: str,
    log_tail: list[str],
    stage: int,
    stage_name: str,
    percent: int,
) -> None:
    """Write a progress + liveness update. Called from the worker loop
    whenever log_tail / stage / percent have actually changed.

    Full-row update — for liveness-only ticks (nothing changed since the
    last successful heartbeat) use :func:`touch_heartbeat` instead to keep
    write volume proportional to genuine progress, not wallclock.
    """
    async with get_session() as s:
        await s.execute(
            update(BuildJobRow)
            .where(BuildJobRow.job_id == job_id)
            .values(
                heartbeat_at=datetime.now(timezone.utc),
                log_tail=list(log_tail),
                stage=stage,
                stage_name=stage_name,
                percent=percent,
            )
        )
        await s.commit()


async def touch_heartbeat(job_id: str) -> None:
    """Bump only ``heartbeat_at`` — proves the worker is still alive without
    rewriting log_tail or stage columns.

    At 500 concurrent builds a 10s full-row heartbeat would be 50 writes/s
    to the same Postgres table; a touch-only update is a narrower UPDATE
    and (because nothing else in the row changes) is cheaper for autovacuum
    to ignore. The sweeper checks ``heartbeat_at`` alone to decide if a
    worker died, so this is functionally equivalent for zombie detection.
    """
    async with get_session() as s:
        await s.execute(
            update(BuildJobRow)
            .where(BuildJobRow.job_id == job_id)
            .values(heartbeat_at=datetime.now(timezone.utc))
        )
        await s.commit()


async def record_stage_transition(
    job_id: str,
    stage: int,
    stage_name: str,
    percent: int,
) -> None:
    """Write a single stage-boundary UPDATE. Separate from heartbeat so we
    don't clobber log_tail when the build hasn't logged anything between
    transitions."""
    async with get_session() as s:
        await s.execute(
            update(BuildJobRow)
            .where(BuildJobRow.job_id == job_id)
            .values(
                stage=stage,
                stage_name=stage_name,
                percent=percent,
                heartbeat_at=datetime.now(timezone.utc),
            )
        )
        await s.commit()


async def complete(
    job_id: str,
    status: str,
    stats: Optional[dict] = None,
    backends: Optional[dict] = None,
    error: Optional[str] = None,
    log_tail: Optional[list[str]] = None,
    domain_snapshot: Optional[dict] = None,
    graph_snapshot: Optional[dict] = None,
) -> None:
    """Terminal UPDATE: status ∈ {done, error}, finished_at, final stats."""
    assert status in ("done", "error"), f"invalid terminal status: {status}"
    async with get_session() as s:
        values: dict = {
            "status": status,
            "finished_at": datetime.now(timezone.utc),
            "heartbeat_at": datetime.now(timezone.utc),
            "percent": 100 if status == "done" else None,
        }
        # Drop None-valued percent (so we don't clobber the last known percent on error)
        values = {k: v for k, v in values.items() if v is not None or k != "percent"}
        if stats is not None:
            values["stats"] = stats
        if backends is not None:
            values["backends"] = backends
        if error is not None:
            values["error"] = error
        if log_tail is not None:
            values["log_tail"] = list(log_tail)
        if domain_snapshot is not None:
            values["domain_snapshot"] = domain_snapshot
        if graph_snapshot is not None:
            values["graph_snapshot"] = graph_snapshot
        await s.execute(
            update(BuildJobRow)
            .where(BuildJobRow.job_id == job_id)
            .values(**values)
        )
        await s.commit()


# ---------------------------------------------------------------------------
# Sweeper — crashed-worker recovery
# ---------------------------------------------------------------------------

async def sweep_stale() -> int:
    """Flip zombie 'running' rows to 'error' and re-queue those still
    within the retry budget.

    A 'running' row is a zombie if ``heartbeat_at < now() - 90s``. We
    increment ``attempt_count`` regardless; if the new count is still
    under :data:`MAX_ATTEMPTS` we reset the row to ``status='queued'`` so
    another worker can pick it up. Otherwise it stays ``status='error'``
    with a permanent error message.

    Returns the number of stale rows touched.
    """
    # One-shot UPDATE with CASE — avoids two round-trips.
    # Note: the unique partial index ``uq_build_jobs_active_per_ws`` permits
    # this re-queue because the only pre-existing active row for this
    # workspace IS the zombie we're re-queuing.
    timeout_clause = f"now() - interval '{HEARTBEAT_TIMEOUT_SECONDS} seconds'"
    async with get_session() as s:
        result = await s.execute(text(f"""
            UPDATE build_jobs
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
        if swept:
            for job_id, attempt, new_status in swept:
                logger.warning(
                    "sweep_stale: job=%s attempt=%d -> %s",
                    job_id, attempt, new_status,
                )
        return len(swept)


# ---------------------------------------------------------------------------
# Snapshotting (configs captured at enqueue time for audit)
# ---------------------------------------------------------------------------

def snapshot_configs_sync() -> tuple[Optional[dict], Optional[dict]]:
    """Best-effort snapshot of active domain + graph config for audit.

    Kept sync (no awaits) because the worker calls this inside
    ``asyncio.to_thread(build_graph, ...)`` via the existing
    ``_snapshot_configs`` helper. Duplicated here so the API can also
    snapshot on enqueue (optional — workers do it again at build time).
    """
    try:
        from src.graph_config import get_graph_config
        from src.kb_config import get_active_kb_config
        cfg = get_active_kb_config()
        p = cfg.profile
        domain = {
            "domain_name": p.domain_name,
            "domain_display_name": p.domain_display_name,
            "organization_name": p.organization_name,
        }
        gc = get_graph_config()
        graph = {
            "embeddings": {
                "model": gc.embeddings.model,
                "similarity_threshold": gc.embeddings.similarity_threshold,
            },
        }
        return domain, graph
    except Exception as exc:
        logger.debug("snapshot_configs_sync failed: %s", exc)
        return None, None
