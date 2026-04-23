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
import os
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

# Heartbeat cadence. The original 2s loop was visibility-oriented (UI sees
# log_tail update quickly) but at 500 concurrent builds = 250 writes/s to
# a single ``build_jobs`` row-set — enough to starve other writes on small
# Neon plans. Default bumped to 10s (env-tunable) and writes now only go to
# Neon when state has actually changed since the last tick — steady-state
# "still running stage 4" doesn't touch the DB at all. Zombie detection
# timeout stays at 90s so a 10s heartbeat is still ~9x safety margin.
_HEARTBEAT_INTERVAL_SECONDS = float(os.environ.get("BUILD_HEARTBEAT_INTERVAL_S", "10"))


async def _heartbeat_loop(
    job_id: str,
    state: _WorkerLogState,
    stop: asyncio.Event,
    interval_seconds: float = _HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """Stream log_tail + liveness to Neon every ``interval_seconds``.

    Runs on the worker's main asyncio loop alongside the ``to_thread``
    build. Exits cleanly when ``stop`` is set. Only writes when the
    in-memory snapshot has changed since the last successful write — this
    caps steady-state DB writes per active build to O(N distinct stages),
    not O(uptime/interval).
    """
    last_snapshot: tuple | None = None
    try:
        while not stop.is_set():
            try:
                snap = state.snapshot()
                # Snapshot is (log_tail_list, stage, stage_name, percent).
                # Convert log_tail (mutable) to a tuple for fast compare.
                log_tail, stage, stage_name, percent = snap
                cmp_key = (tuple(log_tail), stage, stage_name, percent)
                if cmp_key != last_snapshot:
                    await build_queue.heartbeat(
                        job_id=job_id,
                        log_tail=log_tail,
                        stage=stage,
                        stage_name=stage_name,
                        percent=percent,
                    )
                    last_snapshot = cmp_key
                else:
                    # Nothing changed — still need the liveness side-effect
                    # so the sweeper doesn't think we're dead. The cheap
                    # liveness-only write bumps heartbeat_at without touching
                    # log_tail / stage columns, so a partial index on
                    # ``status='running'`` can be exploited.
                    await build_queue.touch_heartbeat(job_id=job_id)
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

        # Cross-store post-build verification. `_run_sync_build` returning
        # cleanly means no exception was raised, but an orchestration that
        # silently skips a backend (e.g. Qdrant timeout swallowed upstream)
        # would still get us here with partial state. Re-check the expected
        # invariants; if any fail, treat this like a build failure so the
        # UI + sweeper see the correct status and retry semantics.
        verification_error = await asyncio.to_thread(
            _verify_build_stores,
            job.workspace_id,
            final_stats,
            job.skip_embeddings,
        )
        if verification_error:
            raise RuntimeError(f"post-build verification failed: {verification_error}")

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
        # Billing deduction (post-commit). Look up workspace owner, check
        # BYOK status, compute cost from the final stats.usage payload, and
        # write a ledger entry. Best-effort — a billing failure MUST NOT
        # mark an otherwise-successful build as errored.
        try:
            await _debit_build_completion(
                job=job,
                stats=final_stats,
                failed=False,
            )
        except Exception as bill_exc:
            logger.warning(
                "billing debit for build %s failed: %s", job.job_id, bill_exc,
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
        # Billing deduction on failed build — forgives small failures and
        # drops the baseline fee, but charges for tokens actually burned.
        try:
            await _debit_build_completion(
                job=job,
                stats=partial_stats or {},
                failed=True,
            )
        except Exception as bill_exc:
            logger.warning(
                "billing debit for failed build %s failed: %s",
                job.job_id, bill_exc,
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


def _verify_build_stores(
    workspace_id: str,
    stats: Optional[dict],
    skip_embeddings: bool,
) -> Optional[str]:
    """Assert the side stores actually hold what the build thinks it wrote.

    Runs after ``_run_sync_build`` returns successfully. Returns an error
    string if any check fails; returns None if everything lines up.

    Two invariants are cheap to verify and cover the common silent-drift
    failure modes:

      - Memgraph node count for the workspace is > 0 (graph actually wrote).
      - Qdrant collection has vectors if embeddings were requested (Qdrant
        accepted the upsert; wasn't silently skipped by a timeout upstream).

    Runs in a worker thread (``asyncio.to_thread``) so the network calls to
    Memgraph + Qdrant don't block the event loop.
    """
    from src.config import USE_MEMGRAPH, USE_NEO4J, USE_QDRANT

    expected_nodes = int((stats or {}).get("total_nodes") or 0)

    if USE_MEMGRAPH:
        try:
            from src.config import (
                MEMGRAPH_DATABASE,
                MEMGRAPH_PASSWORD,
                MEMGRAPH_URI,
                MEMGRAPH_USERNAME,
            )
            from src.infra.memgraph_store import MemgraphGraphStore

            store = MemgraphGraphStore(
                MEMGRAPH_URI, MEMGRAPH_USERNAME, MEMGRAPH_PASSWORD, MEMGRAPH_DATABASE
            )
            ws_stats = store.stats(workspace_id=workspace_id)
            actual_nodes = int(ws_stats.get("total_nodes") or 0)
        except Exception as exc:
            return f"Memgraph verification call failed: {exc}"
        if expected_nodes > 0 and actual_nodes == 0:
            return (
                f"Memgraph reports 0 nodes for workspace {workspace_id} "
                f"but build stats claim {expected_nodes}"
            )
    elif USE_NEO4J:
        # Same check, Neo4j variant. Kept separate so neither driver is
        # imported when the other is configured.
        try:
            from src.config import NEO4J_DATABASE, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USERNAME
            from src.infra.neo4j_store import Neo4jGraphStore

            store = Neo4jGraphStore(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE)
            ws_stats = store.stats(workspace_id=workspace_id)
            actual_nodes = int(ws_stats.get("total_nodes") or 0)
        except Exception as exc:
            return f"Neo4j verification call failed: {exc}"
        if expected_nodes > 0 and actual_nodes == 0:
            return (
                f"Neo4j reports 0 nodes for workspace {workspace_id} "
                f"but build stats claim {expected_nodes}"
            )

    if USE_QDRANT and not skip_embeddings and expected_nodes > 0:
        try:
            from src.config import (
                QDRANT_API_KEY,
                QDRANT_SHARED_COLLECTION,
                QDRANT_URL,
            )
            from qdrant_client import QdrantClient
            from src.graph_builder.builder import _short_wid

            client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30)
            if QDRANT_SHARED_COLLECTION:
                # Shared-collection mode: count points filtered by
                # workspace_id instead of relying on collection-level total.
                try:
                    from qdrant_client.models import (
                        FieldCondition,
                        Filter,
                        MatchValue,
                    )
                    flt = Filter(must=[FieldCondition(
                        key="workspace_id", match=MatchValue(value=workspace_id)
                    )])
                    resp = client.count(
                        collection_name=QDRANT_SHARED_COLLECTION,
                        count_filter=flt,
                        exact=True,
                    )
                    vectors_count = int(getattr(resp, "count", 0) or 0)
                    collection = QDRANT_SHARED_COLLECTION
                except Exception as exc:
                    return (
                        f"Qdrant filtered count on '{QDRANT_SHARED_COLLECTION}' "
                        f"failed: {exc}"
                    )
            else:
                collection = f"kb-{_short_wid(workspace_id)}"
                try:
                    info = client.get_collection(collection)
                    vectors_count = int(getattr(info, "points_count", 0) or 0)
                except Exception as exc:
                    return f"Qdrant collection '{collection}' missing after build: {exc}"
        except Exception as exc:
            return f"Qdrant verification call failed: {exc}"
        if vectors_count == 0:
            return (
                f"Qdrant collection '{collection}' has 0 points after build "
                f"(expected ~{expected_nodes})"
            )

    return None


async def _debit_build_completion(
    job: ClaimedJob,
    stats: dict,
    failed: bool,
) -> None:
    """Compute cost from final stats + BYOK status + workspace owner, then
    write a ledger entry and (for trial users) bump the trial build counter.

    Called from ``run_build_async`` on both success and failure paths.
    BYOK detection: checks ``workspace_api_keys`` row existence. When
    present, LLM+embedding tokens are waived. Baseline + storage fees are
    independent of BYOK.
    """
    import uuid as _uuid

    from sqlalchemy import select
    from src.billing import cost_build, debit
    from src.billing.ledger import bump_trial_counter, get_account
    from src.infra.db import get_session
    from src.infra.db_models import Workspace, WorkspaceApiKey

    ws_uuid = _uuid.UUID(job.workspace_id)

    async with get_session() as s:
        owner_id = (await s.execute(
            select(Workspace.user_id).where(Workspace.id == ws_uuid)
        )).scalar_one_or_none()
        if owner_id is None:
            logger.warning(
                "billing: workspace %s has no owner — skipping debit",
                job.workspace_id,
            )
            return
        has_byok = (await s.execute(
            select(WorkspaceApiKey.workspace_id).where(
                WorkspaceApiKey.workspace_id == ws_uuid
            )
        )).scalar_one_or_none() is not None

    usage = (stats or {}).get("usage") or {}
    credits = cost_build(usage=usage, has_byok=has_byok, failed=failed)

    await debit(
        owner_id,
        credits,
        reason="build" if not failed else "build_failed",
        source_type="build_job",
        source_id=job.job_id,
        actor_type="system",
        metadata={
            "workspace_id": job.workspace_id,
            "has_byok": has_byok,
            "failed": failed,
            "status": "error" if failed else "done",
        },
    )
    if not failed:
        # Trial users: increment the successful-build counter. No-op for
        # non-trial; atomic UPDATE scoped by ``plan_tier='trial'``.
        try:
            await bump_trial_counter(owner_id, kind="build")
        except Exception as exc:
            logger.debug("trial build counter bump failed: %s", exc)


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
    # Storage proration runs much less often — once per ~30 minutes is
    # plenty since the sweeper is idempotent (dedupe by workspace_id+date).
    last_storage_sweep_ts = 0.0
    storage_sweep_interval = 30 * 60.0  # 30 minutes
    # Workspace-deletion saga retries. Picks up soft-deleted workspaces
    # whose Memgraph/Qdrant/Blob cleanup failed on the inline attempt and
    # retries the cascade. 60s interval balances "fast enough that orphans
    # clear quickly" against "don't hammer side stores during an outage."
    last_deletion_sweep_ts = 0.0
    deletion_sweep_interval = 60.0

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

        # Daily storage proration. Idempotent via per-(workspace, day)
        # dedupe; running every 30 min means we pick up new workspaces
        # within half an hour of their first completed build.
        if now - last_storage_sweep_ts >= storage_sweep_interval:
            try:
                from src.billing.storage_sweeper import sweep_daily_storage
                emitted = await sweep_daily_storage()
                if emitted:
                    logger.info("storage sweep emitted %d debit(s)", emitted)
            except Exception as exc:
                logger.warning("storage sweep failed: %s", exc)
            last_storage_sweep_ts = now

        # Workspace-deletion saga retries. Idempotent — if the side stores
        # are healthy we clear a few rows per tick; if an upstream is
        # throwing we leave rows alone and try again next sweep.
        if now - last_deletion_sweep_ts >= deletion_sweep_interval:
            try:
                from src.api.workspace_routes import sweep_pending_deletions
                cleared = await sweep_pending_deletions()
                if cleared:
                    logger.info("deletion sweep cleared %d workspace(s)", cleared)
            except Exception as exc:
                logger.warning("deletion sweep failed: %s", exc)
            last_deletion_sweep_ts = now

        try:
            await asyncio.sleep(CLAIM_POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            break

    logger.info("Build worker exiting cleanly.")
