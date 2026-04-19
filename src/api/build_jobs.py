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
_running_job_id: Optional[str] = None


def get_job(job_id: str) -> Optional[BuildJob]:
    return _jobs.get(job_id)


def current_running_job_id() -> Optional[str]:
    return _running_job_id


def _set_stage(job: BuildJob, stage: int) -> None:
    job.stage = stage
    job.stage_name = _STAGE_NAMES.get(stage, f"Stage {stage}")
    # Roughly even-weighted: 5 stages → 20% each. Stage 1..5 => 20,40,60,80,100.
    job.percent = min(100, stage * 20)


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
    global _running_job_id

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

    try:
        # Ensure config caches are fresh so edits to yaml take effect.
        from src.graph_config import get_graph_config
        from src.kb_config import reset_active_kb_config
        get_graph_config.cache_clear()
        reset_active_kb_config()

        from src.graph_builder.builder import KnowledgeGraph, build_graph
        kg = build_graph(
            use_llm_cross_links=not job.skip_llm_cross_links,
            skip_embeddings=job.skip_embeddings,
        )

        # Hot-swap the running API's graph
        try:
            from src.api.routes import set_knowledge_graph
            set_knowledge_graph(kg)
        except Exception as exc:
            job.log_tail.append(f"[warn] failed to hot-swap kg into API: {exc}")

        _set_stage(job, 5)
        job.percent = 100
        job.status = "done"
        job.finished_at = time.time()
        elapsed = job.finished_at - job.started_at
        job.log_tail.append(f"[job {job.job_id}] build complete in {elapsed:.1f}s")
    except Exception as exc:
        job.error = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
        job.status = "error"
        job.finished_at = time.time()
        job.log_tail.append(f"[error] {exc}")
        logger.exception("Build job %s failed", job.job_id)
    finally:
        for t in targets:
            t.removeHandler(handler)
        with _state_lock:
            global _running_job_id
            _running_job_id = None


def start_build(
    skip_embeddings: bool = False,
    skip_llm_cross_links: bool = False,
) -> BuildJob:
    """Start a background build. Raises RuntimeError if a build is already running."""
    global _running_job_id
    with _state_lock:
        if _running_job_id is not None:
            raise RuntimeError(
                f"A build is already running (job_id={_running_job_id}). "
                "Wait for it to finish before starting another."
            )
        job_id = uuid.uuid4().hex[:12]
        job = BuildJob(
            job_id=job_id,
            skip_embeddings=skip_embeddings,
            skip_llm_cross_links=skip_llm_cross_links,
        )
        _jobs[job_id] = job
        _running_job_id = job_id

    thread = threading.Thread(target=_run_build, args=(job,), daemon=True, name=f"build-{job_id}")
    thread.start()
    return job
