"""
Background build-job runner.

Wraps src.graph_builder.builder.build_graph() in a background thread so the
HTTP /build endpoint can return immediately with a job_id and the frontend
can poll GET /build/{job_id} for progress.

Design:
  - Single-flight: only one build runs at a time; additional POSTs return 409.
  - Progress reporting: a logging.Handler installed on the `src.graph_builder`
    logger captures each line, tails the last 50 into job.log_tail, and parses
    "Step N/5" markers to update job.stage / job.percent.
  - Completion: on success the newly built graph is loaded into the live API
    server via src.api.routes.set_knowledge_graph().
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional

logger = logging.getLogger(__name__)

BuildStatus = Literal["queued", "running", "done", "error"]

_STAGE_NAMES = {
    0: "Queued",
    1: "Parse KB files",
    2: "Extract nodes",
    3: "Build structural edges",
    4: "Generate embeddings",
    5: "Persist graph",
}


@dataclass
class BuildJob:
    job_id: str
    workspace_id: str
    status: BuildStatus = "queued"
    stage: int = 0
    stage_name: str = _STAGE_NAMES[0]
    percent: int = 0
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    error: Optional[str] = None
    log_tail: deque = field(default_factory=lambda: deque(maxlen=50))
    skip_embeddings: bool = False
    skip_llm_cross_links: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["log_tail"] = list(self.log_tail)
        return d


_jobs: dict[str, BuildJob] = {}
_state_lock = threading.Lock()
# Per-workspace single-flight: workspace_id → job_id currently running.
_running_by_ws: dict[str, str] = {}


def get_job(job_id: str) -> Optional[BuildJob]:
    return _jobs.get(job_id)


def current_running_job_id(workspace_id: str | None = None) -> Optional[str]:
    """If workspace_id given, return that workspace's running job; else any."""
    if workspace_id is None:
        return next(iter(_running_by_ws.values()), None)
    return _running_by_ws.get(workspace_id)


def _set_stage(job: BuildJob, stage: int) -> None:
    job.stage = stage
    job.stage_name = _STAGE_NAMES.get(stage, f"Stage {stage}")
    # Roughly even-weighted: 5 stages → 20% each. Stage 1..5 => 20,40,60,80,100.
    job.percent = min(100, stage * 20)
    _persist_job(job)


# ---------------------------------------------------------------------------
# Neon persistence (write-through)
# ---------------------------------------------------------------------------

def _persist_job(job: BuildJob, stats: dict | None = None, backends: dict | None = None) -> None:
    """Schedule an async UPSERT of *job* onto the main event loop. Non-blocking."""
    from src.config import USE_NEON
    if not USE_NEON:
        return
    try:
        from src.infra.db import fire_and_forget
        fire_and_forget(_upsert_job_async(job, stats=stats, backends=backends))
    except Exception as exc:
        logger.debug("build-job persistence skipped: %s", exc)


def _snapshot_configs() -> tuple[dict | None, dict | None]:
    """Best-effort snapshot of current domain + graph config at build time."""
    try:
        from src.kb_config import get_active_kb_config
        from src.graph_config import get_graph_config
        cfg = get_active_kb_config()
        p = cfg.profile
        domain = {
            "domain_name": p.domain_name,
            "domain_display_name": p.domain_display_name,
            "organization_name": p.organization_name,
            "knowledge_focus_examples": p.knowledge_focus_examples,
            "tool_focus_examples": p.tool_focus_examples,
        }
        gc = get_graph_config()
        graph = {
            "embeddings": {
                "model": gc.embeddings.model,
                "similarity_threshold": gc.embeddings.similarity_threshold,
                "max_related_edges_per_node": gc.embeddings.max_related_edges_per_node,
                "input_max_chars": gc.embeddings.input_max_chars,
                "dimensions": gc.embeddings.dimensions,
                "skip_related_to_types": list(gc.embeddings.skip_related_to_types),
            },
            "cross_kb": {
                "auto_threshold": gc.cross_kb.auto_threshold,
                "embed_weight": gc.cross_kb.embed_weight,
                "cooccur_weight": gc.cross_kb.cooccur_weight,
                "max_links_per_chapter": gc.cross_kb.max_links_per_chapter,
            },
        }
        return domain, graph
    except Exception as exc:
        logger.debug("snapshot_configs failed: %s", exc)
        return None, None


async def _upsert_job_async(job: BuildJob, stats: dict | None = None, backends: dict | None = None) -> None:
    """INSERT...ON CONFLICT UPDATE for build_jobs row."""
    try:
        from sqlalchemy.dialects.postgresql import insert
        from src.infra.db import get_session
        from src.infra.db_models import BuildJobRow

        domain_snap, graph_snap = _snapshot_configs()

        async with get_session() as s:
            stmt = insert(BuildJobRow).values(
                job_id=job.job_id,
                workspace_id=uuid.UUID(job.workspace_id),
                status=job.status,
                stage=job.stage,
                stage_name=job.stage_name,
                percent=job.percent,
                started_at=datetime.fromtimestamp(job.started_at, tz=timezone.utc),
                finished_at=(
                    datetime.fromtimestamp(job.finished_at, tz=timezone.utc)
                    if job.finished_at else None
                ),
                error=job.error,
                log_tail=list(job.log_tail),
                skip_embeddings=job.skip_embeddings,
                skip_llm_cross_links=job.skip_llm_cross_links,
                domain_snapshot=domain_snap,
                graph_snapshot=graph_snap,
                stats=stats,
                backends=backends,
            )
            from sqlalchemy import func as _func
            stmt = stmt.on_conflict_do_update(
                index_elements=["job_id"],
                set_={
                    "status": stmt.excluded.status,
                    "stage": stmt.excluded.stage,
                    "stage_name": stmt.excluded.stage_name,
                    "percent": stmt.excluded.percent,
                    "finished_at": stmt.excluded.finished_at,
                    "error": stmt.excluded.error,
                    "log_tail": stmt.excluded.log_tail,
                    # COALESCE so a later progress update with NULL stats
                    # cannot clobber stats/backends already written at terminal state.
                    "stats": _func.coalesce(stmt.excluded.stats, BuildJobRow.stats),
                    "backends": _func.coalesce(stmt.excluded.backends, BuildJobRow.backends),
                },
            )
            await s.execute(stmt)
            await s.commit()
    except Exception as exc:
        logger.warning("Neon upsert for build job %s failed: %s", job.job_id, exc)


class _JobLogHandler(logging.Handler):
    """Captures log lines into job.log_tail and derives stage/percent."""

    def __init__(self, job: BuildJob) -> None:
        super().__init__(level=logging.INFO)
        self.job = job

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        self.job.log_tail.append(msg)

        # Match "Step N/5" markers from builder.py
        raw = record.getMessage()
        if "Step 1/5" in raw:
            _set_stage(self.job, 1)
        elif "Step 2/5" in raw:
            _set_stage(self.job, 2)
        elif "Step 3/5" in raw:
            _set_stage(self.job, 3)
        elif "Step 4/5" in raw:
            _set_stage(self.job, 4)
        elif "Step 5/5" in raw:
            _set_stage(self.job, 5)


def _run_build(job: BuildJob) -> None:
    # Attach a log handler so we capture all graph_builder + related output
    handler = _JobLogHandler(job)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s", "%H:%M:%S"))

    targets = [
        logging.getLogger("src.graph_builder"),
        logging.getLogger("src.graph_builder.builder"),
        logging.getLogger("src.graph_builder.embeddings"),
        logging.getLogger("src.graph_builder.edges"),
        logging.getLogger("src.graph_builder.extractor"),
        logging.getLogger("src.graph_builder.parser"),
        logging.getLogger("src.graph_builder.cross_kb_mapper"),
    ]
    for t in targets:
        t.addHandler(handler)
        t.setLevel(logging.INFO)

    job.status = "running"
    job.started_at = time.time()
    job.log_tail.append(f"[job {job.job_id}] build started")
    _persist_job(job)

    try:
        # Ensure config caches are fresh so edits to yaml take effect.
        from src.graph_config import get_graph_config
        from src.kb_config import reset_active_kb_config
        get_graph_config.cache_clear()
        reset_active_kb_config()

        # Install workspace context for the pipeline to pick up.
        from src.workspace_context import set_current_workspace
        set_current_workspace(job.workspace_id)

        # Collect the list of active knowledge + tool files from Neon.
        knowledge_paths, tool_paths = _collect_workspace_files(job.workspace_id)
        if not knowledge_paths and not tool_paths:
            raise RuntimeError(
                f"Workspace {job.workspace_id} has no uploaded files. "
                "Upload at least one knowledge or tool JSON before building."
            )

        from src.graph_builder.builder import build_graph
        kg = build_graph(
            use_llm_cross_links=not job.skip_llm_cross_links,
            skip_embeddings=job.skip_embeddings,
            workspace_id=job.workspace_id,
            knowledge_paths=knowledge_paths,
            tool_paths=tool_paths,
        )

        # Cache the live kg for this workspace so the API can serve queries.
        try:
            from src.api.routes import set_knowledge_graph
            set_knowledge_graph(kg, workspace_id=job.workspace_id)
        except Exception as exc:
            job.log_tail.append(f"[warn] failed to cache kg into API: {exc}")

        _set_stage(job, 5)
        job.percent = 100
        job.status = "done"
        job.finished_at = time.time()
        elapsed = job.finished_at - job.started_at
        job.log_tail.append(f"[job {job.job_id}] build complete in {elapsed:.1f}s")

        # Capture final stats + backends for the audit row
        try:
            stats = kg.stats() if kg is not None else None
            backends = (stats or {}).get("backends")
        except Exception:
            stats, backends = None, None
        _persist_job(job, stats=stats, backends=backends)
    except Exception as exc:
        job.error = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
        job.status = "error"
        job.finished_at = time.time()
        job.log_tail.append(f"[error] {exc}")
        logger.exception("Build job %s failed", job.job_id)
        _persist_job(job)
    finally:
        for t in targets:
            t.removeHandler(handler)
        with _state_lock:
            _running_by_ws.pop(job.workspace_id, None)


def _collect_workspace_files(workspace_id: str):
    """Fetch active file local paths for a workspace, partitioned by kb_source.

    This function is *sync* because it's called from the build thread.  We open
    a scratch asyncio loop since SQLAlchemy async requires one.
    """
    import asyncio
    from pathlib import Path
    from sqlalchemy import select
    from src.infra.db import get_session
    from src.infra.db_models import WorkspaceFile

    async def _fetch():
        async with get_session() as s:
            r = await s.execute(
                select(WorkspaceFile)
                .where(WorkspaceFile.workspace_id == uuid.UUID(workspace_id))
                .where(WorkspaceFile.active.is_(True))
                .order_by(WorkspaceFile.created_at)
            )
            rows = r.scalars().all()
        return rows

    rows = asyncio.run(_fetch())
    knowledge_paths: list[Path] = []
    tool_paths: list[Path] = []
    for r in rows:
        if not r.local_path:
            continue
        p = Path(r.local_path)
        if not p.exists():
            continue
        if r.kb_source == "knowledge":
            knowledge_paths.append(p)
        elif r.kb_source == "tool":
            tool_paths.append(p)
    return knowledge_paths, tool_paths


def start_build(
    workspace_id: str,
    skip_embeddings: bool = False,
    skip_llm_cross_links: bool = False,
) -> BuildJob:
    """Start a background build for *workspace_id*.

    Raises RuntimeError if this workspace already has a running build.
    Builds for other workspaces run concurrently.
    """
    with _state_lock:
        if workspace_id in _running_by_ws:
            raise RuntimeError(
                f"A build is already running for workspace {workspace_id} "
                f"(job_id={_running_by_ws[workspace_id]}). "
                "Wait for it to finish before starting another."
            )
        job_id = uuid.uuid4().hex[:12]
        job = BuildJob(
            job_id=job_id,
            workspace_id=workspace_id,
            skip_embeddings=skip_embeddings,
            skip_llm_cross_links=skip_llm_cross_links,
        )
        _jobs[job_id] = job
        _running_by_ws[workspace_id] = job_id

    thread = threading.Thread(target=_run_build, args=(job,), daemon=True, name=f"build-{job_id}")
    thread.start()
    return job
