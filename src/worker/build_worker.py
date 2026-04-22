"""
Durable build-worker process.

Polls Neon via ``src.api.build_queue`` for queued jobs, runs one at a time
(module-level semaphore enforces the invariant), streams log_tail +
heartbeat back to the DB every 2 seconds, and commits terminal state on
completion. Resilient to mid-flight crashes — the sweeper in any worker
will re-queue zombie rows after the heartbeat timeout.

Launched via:

    APP_MODE=worker python -m src.entrypoint

One build per worker process. Scale by adding more worker **processes**
(or Render instances), not more concurrency inside the process — the
graph config + kb_config caches at module scope would race otherwise.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from src.api import build_queue
from src.api.build_queue import (
    CLAIM_POLL_INTERVAL_SECONDS,
    ClaimedJob,
    make_worker_id,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Runtime state (single-worker invariant)
# ---------------------------------------------------------------------------

# One build per worker process — the graph_builder modules have
# module-level caches (graph_config, kb_config) that are cache_clear()'d
# at the start of each build. Running two builds concurrently in the same
# process would race on those clears.
_build_semaphore = asyncio.Semaphore(1)

# Drain flag: set by the SIGTERM handler. While draining, the claim loop
# stops taking new jobs; the in-flight build (if any) runs to completion.
_draining = False


# ---------------------------------------------------------------------------
# Log capture — same shape as the old in-API _JobLogHandler, except the
# heartbeat coroutine drains the deque under a lock.
# ---------------------------------------------------------------------------

_STAGE_NAMES = {
    0: "Queued",
    1: "Parse KB files",
    2: "Extract nodes",
    3: "Build structural edges",
    4: "Generate embeddings",
    5: "Persist graph",
}


class _WorkerLogState:
    """Thread-safe container for the in-flight build's log tail + current
    stage. Written from the build thread (via ``_WorkerLogHandler``), read
    from the asyncio heartbeat coroutine on the worker's main loop.
    """
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._log_tail: deque[str] = deque(maxlen=50)
        self.stage: int = 0
        self.stage_name: str = _STAGE_NAMES[0]
        self.percent: int = 0

    def append(self, line: str) -> None:
        with self._lock:
            self._log_tail.append(line)

    def set_stage(self, stage: int) -> None:
        with self._lock:
            self.stage = stage
            self.stage_name = _STAGE_NAMES.get(stage, f"Stage {stage}")
            self.percent = min(100, stage * 20)

    def snapshot(self) -> tuple[list[str], int, str, int]:
        with self._lock:
            return list(self._log_tail), self.stage, self.stage_name, self.percent


class _WorkerLogHandler(logging.Handler):
    """Captures log lines from the graph_builder loggers into the shared
    log-state object. Parses "Step N/5" markers to update stage. Called
    from the build thread (``asyncio.to_thread``), not the main loop.

    Dedups stage transitions: a given stage N only fires once per build,
    even if the same log line reaches the handler twice (e.g. via logger
    hierarchy propagation). Without this, ``mark_stage`` would be called
    twice per transition, scrambling ``stage_timings_ms``.
    """

    def __init__(self, state: _WorkerLogState, schedule_stage_update) -> None:
        super().__init__(level=logging.INFO)
        self.state = state
        self._schedule_stage_update = schedule_stage_update
        self._last_triggered_stage: int = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        self.state.append(msg)

        raw = record.getMessage()
        for i in range(1, 6):
            if f"Step {i}/5" in raw:
                # Dedup: ignore a stage that has already been announced.
                # Stages can only advance, never go backwards.
                if i <= self._last_triggered_stage:
                    break
                self._last_triggered_stage = i
                stage_name = _STAGE_NAMES[i]
                self.state.set_stage(i)
                # Record the transition into the BuildMetrics accumulator
                # so ``stats.timings.stages_ms`` is populated at finalize.
                # This runs in the build thread (``asyncio.to_thread``)
                # whose context inherits ``build_metrics_var`` from the
                # main loop via copy_context().
                try:
                    from src.observability.usage import mark_stage
                    mark_stage(stage_name)
                except Exception:
                    pass
                # Schedule a stage-boundary DB write (non-blocking).
                try:
                    self._schedule_stage_update(i, stage_name, min(100, i * 20))
                except Exception:
                    pass
                break


# ---------------------------------------------------------------------------
# Heartbeat loop
# ---------------------------------------------------------------------------

async def _heartbeat_loop(
    job_id: str,
    state: _WorkerLogState,
    stop: asyncio.Event,
    interval_seconds: float = 2.0,
) -> None:
    """Stream log_tail + liveness to Neon every ``interval_seconds``.

    Runs on the worker's main asyncio loop alongside the ``to_thread``
    build. Exits cleanly when ``stop`` is set.
    """
    try:
        while not stop.is_set():
            try:
                log_tail, stage, stage_name, percent = state.snapshot()
                await build_queue.heartbeat(
                    job_id=job_id,
                    log_tail=log_tail,
                    stage=stage,
                    stage_name=stage_name,
                    percent=percent,
                )
            except Exception as exc:
                # A failed heartbeat is not fatal — the sweeper will pick
                # up the job if the failure persists past HEARTBEAT_TIMEOUT.
                logger.warning("heartbeat for %s failed: %s", job_id, exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass
    except asyncio.CancelledError:
        return


# ---------------------------------------------------------------------------
# Build execution
# ---------------------------------------------------------------------------

async def run_build_async(job: ClaimedJob, worker_id: str | None = None) -> None:
    """Run the (synchronous) build pipeline for a claimed job.

    - Installs the per-build metrics contextvar (from usage.py).
    - Collects workspace sources + computes input bytes.
    - Runs ``build_graph`` in ``asyncio.to_thread``.
    - Streams log_tail + stage transitions to Neon via heartbeat coroutine.
    - On success: caches kg locally, writes terminal stats + backends.
    - On failure: writes terminal error + partial stats (tokens burned).
    """
    state = _WorkerLogState()
    stop_heartbeat = asyncio.Event()
    loop = asyncio.get_running_loop()

    # Stage-transition hook: called from the build thread via the log handler.
    # Schedule a record_stage_transition() onto the main loop (thread-safe).
    def schedule_stage_update(stage: int, stage_name: str, percent: int) -> None:
        asyncio.run_coroutine_threadsafe(
            build_queue.record_stage_transition(
                job_id=job.job_id,
                stage=stage,
                stage_name=stage_name,
                percent=percent,
            ),
            loop,
        )

    handler = _WorkerLogHandler(state, schedule_stage_update)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(message)s", "%H:%M:%S"))

    # Attach ONLY to the parent logger — children propagate up the
    # hierarchy, so a log from ``src.graph_builder.builder`` still reaches
    # this handler. Attaching to both parent and children would make the
    # handler fire twice per message (double-counting stage transitions).
    targets = [logging.getLogger("src.graph_builder")]
    for t in targets:
        t.addHandler(handler)
        t.setLevel(logging.INFO)

    # Start the heartbeat loop early so the job's log_tail is visible to
    # the UI from the moment the build starts.
    hb_task = asyncio.create_task(_heartbeat_loop(job.job_id, state, stop_heartbeat))

    # Per-build metrics accumulator (same contract as the old in-API build).
    from src.observability.usage import (
        BuildMetrics,
        build_metrics_var,
        finalize_stages,
    )
    metrics = BuildMetrics(build_started_perf=time.perf_counter())
    metrics_token = build_metrics_var.set(metrics)

    state.append(f"[job {job.job_id}] build started on worker {worker_id or make_worker_id()}")

    try:
        await asyncio.to_thread(_run_sync_build, job, metrics, state)

        # Capture final stats + backends, fold usage metrics in.
        from src.api.build_jobs import _merge_metrics_into_stats
        finalize_stages()

        # kg.stats() + backends come from the thread-local kg, passed back
        # via a small contextvar. See _run_sync_build below.
        kg_stats = _LAST_BUILD_RESULT.get("stats")
        backends = (kg_stats or {}).get("backends")
        final_stats = _merge_metrics_into_stats(kg_stats, metrics)
        domain_snap = _LAST_BUILD_RESULT.get("domain_snapshot")
        graph_snap = _LAST_BUILD_RESULT.get("graph_snapshot")
        log_tail, _s, _sn, _p = state.snapshot()
        state.append(f"[job {job.job_id}] build complete")

        await build_queue.complete(
            job_id=job.job_id,
            status="done",
            stats=final_stats,
            backends=backends,
            log_tail=log_tail + [f"[job {job.job_id}] build complete"],
            domain_snapshot=domain_snap,
            graph_snapshot=graph_snap,
        )
    except Exception as exc:
        err = f"{exc.__class__.__name__}: {exc}\n{traceback.format_exc()}"
        logger.exception("Build job %s failed", job.job_id)
        state.append(f"[error] {exc}")
        try:
            from src.api.build_jobs import _merge_metrics_into_stats
            finalize_stages()
            partial_stats = _merge_metrics_into_stats(None, metrics)
        except Exception:
            partial_stats = None
        log_tail, _s, _sn, _p = state.snapshot()
        try:
            await build_queue.complete(
                job_id=job.job_id,
                status="error",
                stats=partial_stats,
                error=err,
                log_tail=log_tail,
            )
        except Exception as persist_exc:
            logger.error(
                "Failed to record error state for %s: %s", job.job_id, persist_exc
            )
    finally:
        try:
            build_metrics_var.reset(metrics_token)
        except Exception:
            pass
        stop_heartbeat.set()
        try:
            await hb_task
        except Exception:
            pass
        for t in targets:
            t.removeHandler(handler)
        _LAST_BUILD_RESULT.clear()


# Result hand-off between the sync build thread and the async caller.
# Module-level because the semaphore ensures only one build runs at a
# time — no concurrent writers.
_LAST_BUILD_RESULT: dict = {}


def _run_sync_build(job: ClaimedJob, metrics, state: _WorkerLogState) -> None:
    """Synchronous build body. Runs inside ``asyncio.to_thread`` so the
    main event loop stays free for the heartbeat coroutine.

    Mirrors the old ``_run_build`` but without the thread-spawn, without
    the ``fire_and_forget`` Neon writes (those are now awaited by the
    caller via ``build_queue`` helpers), and without the ``_persist_job``
    progress writes (the heartbeat coroutine handles those).
    """
    # Capture workspace-chosen models into metrics for the stats payload.
    try:
        from src.config import EMBEDDING_MODEL, LLM_MODEL
        from src.graph_config import get_graph_config
        from src.infra.workspace_llm import get_workspace_llm_model_sync
        ws_llm = get_workspace_llm_model_sync(job.workspace_id)
        metrics.llm_model = ws_llm or LLM_MODEL
        cfg = get_graph_config()
        metrics.embedding_model = cfg.embeddings.model or EMBEDDING_MODEL
    except Exception as exc:
        logger.debug("model capture for stats failed: %s", exc)

    # Config caches need to be fresh so yaml edits take effect per-build.
    from src.graph_config import get_graph_config
    from src.kb_config import reset_active_kb_config
    get_graph_config.cache_clear()
    reset_active_kb_config()

    # Install workspace context for the pipeline to pick up.
    from src.workspace_context import set_current_workspace
    set_current_workspace(job.workspace_id)

    # Pull the workspace's active KB files from Blob. The worker has its
    # own event loop (the one running run_build_async), so we reach it via
    # run_coroutine_threadsafe — same shape the old build did, but pointing
    # at the worker's loop, not an API's captured one.
    #
    # NOTE: The existing _collect_workspace_sources helper at
    # src/api/build_jobs.py already does this via get_main_loop(). We stamp
    # the worker's loop as the "main" loop on startup (see worker_main()),
    # so the call works unchanged.
    from src.api.build_jobs import _collect_workspace_sources, _snapshot_configs
    knowledge_sources, tool_sources = _collect_workspace_sources(job.workspace_id)
    if not knowledge_sources and not tool_sources:
        raise RuntimeError(
            f"Workspace {job.workspace_id} has no uploaded files. "
            "Upload at least one knowledge or tool JSON before building."
        )

    metrics.inputs_knowledge_files = len(knowledge_sources)
    metrics.inputs_tool_files = len(tool_sources)
    metrics.inputs_total_bytes = sum(
        len(b) for _n, b in knowledge_sources + tool_sources
    )

    from src.graph_builder.builder import build_graph
    kg = build_graph(
        use_llm_cross_links=not job.skip_llm_cross_links,
        skip_embeddings=job.skip_embeddings,
        workspace_id=job.workspace_id,
        knowledge_sources=knowledge_sources,
        tool_sources=tool_sources,
    )

    # Cache the live kg in this worker — not strictly necessary (the worker
    # rarely queries), but keeps the contract symmetric with the old code.
    try:
        from src.api.routes import set_knowledge_graph
        set_knowledge_graph(kg, workspace_id=job.workspace_id)
    except Exception:
        pass

    state.set_stage(5)

    try:
        kg_stats = kg.stats() if kg is not None else None
    except Exception:
        kg_stats = None
    domain_snap, graph_snap = _snapshot_configs()
    _LAST_BUILD_RESULT.update({
        "stats": kg_stats,
        "domain_snapshot": domain_snap,
        "graph_snapshot": graph_snap,
    })


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _install_signal_handlers() -> None:
    def _on_signal(signum, _frame):
        global _draining
        logger.warning("Signal %s received — draining.", signum)
        _draining = True

    # SIGTERM is what Render sends for graceful shutdown. SIGINT handles
    # local Ctrl-C. SIGHUP is occasionally used for reload — treat as drain.
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, AttributeError):
            # Not on main thread / not supported on this OS.
            pass


async def worker_main() -> None:
    """Long-running claim loop. Idempotent, crash-safe, drain-aware."""
    from src.infra import db as _db

    # Initialise Neon engine + ensure tables/columns/indexes exist. Harmless
    # if the API already did this on another instance; idempotent.
    await _db.init_db()

    # The existing _collect_workspace_sources() at src/api/build_jobs.py:345
    # uses ``run_coroutine_threadsafe(_fetch(), get_main_loop())`` to hop
    # from the build thread back to an async context. Register this
    # worker's main loop as the "main loop" so that helper works without
    # needing to be rewritten.
    _db.set_main_loop(asyncio.get_running_loop())

    worker_id = make_worker_id()
    logger.info("Build worker started: %s", worker_id)
    _install_signal_handlers()

    last_sweep_ts = 0.0
    sweep_interval = 30.0

    while not _draining:
        async with _build_semaphore:
            try:
                job = await build_queue.claim_next(worker_id)
            except Exception as exc:
                logger.warning("claim_next failed: %s", exc)
                job = None

            if job is not None:
                logger.info(
                    "Claimed job %s for workspace %s (attempt %d)",
                    job.job_id, job.workspace_id, job.attempt_count,
                )
                await run_build_async(job, worker_id=worker_id)
                continue

        # No job to run — opportunistic sweep of stale runners, then sleep.
        now = time.monotonic()
        if now - last_sweep_ts >= sweep_interval:
            try:
                n = await build_queue.sweep_stale()
                if n:
                    logger.info("sweep_stale: recovered %d zombie job(s)", n)
            except Exception as exc:
                logger.warning("sweep_stale failed: %s", exc)
            last_sweep_ts = now

        try:
            await asyncio.sleep(CLAIM_POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            break

    logger.info("Build worker exiting cleanly.")
