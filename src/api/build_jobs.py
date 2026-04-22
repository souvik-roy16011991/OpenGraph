"""
Shared build-pipeline helpers.

Previously this module ran the build inside the API process (in-memory
``_jobs`` dict + daemon ``threading.Thread``). That's incompatible with
horizontal scaling — see the horizontal-scaling plan.

Now the build runs in a dedicated worker process (``src.worker.build_worker``)
and this module only holds the three helpers the worker still calls:

- :func:`_collect_workspace_sources` — pull active KB files from Vercel Blob
  (one per active row in ``WorkspaceFile``). Runs from the build thread
  (``asyncio.to_thread``) and hops to the worker's event loop via
  ``run_coroutine_threadsafe``. The worker registers its loop as the
  "main loop" on startup so ``get_main_loop()`` works transparently.
- :func:`_snapshot_configs` — freeze the workspace's active domain + graph
  config into audit snapshots, written onto ``BuildJobRow`` at completion.
- :func:`_merge_metrics_into_stats` — fold the ``BuildMetrics`` accumulator
  into the graph stats payload (used by both the worker and any legacy
  CLI that runs the build in-process).

Everything else — ``BuildJob`` dataclass, ``_jobs`` registry, single-flight
lock, ``_JobLogHandler``, ``_run_build``, ``start_build`` — has been
replaced by :mod:`src.api.build_queue` + :mod:`src.worker.build_worker`.
Routes that used to import ``start_build`` now call ``build_queue.enqueue``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Snapshotting (audit) + metrics fold-in
# ---------------------------------------------------------------------------

def _snapshot_configs() -> tuple[dict | None, dict | None]:
    """Best-effort snapshot of current domain + graph config at build time."""
    try:
        from src.graph_config import get_graph_config
        from src.kb_config import get_active_kb_config
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


def _merge_metrics_into_stats(base: dict | None, metrics) -> dict:
    """Fold a ``BuildMetrics`` into the existing ``kg.stats()`` payload.

    Never drops existing keys. Added keys: ``usage``, ``inputs``, ``models``,
    ``timings``. Called on both success and error paths so partial runs still
    surface the tokens they burned.
    """
    out: dict = dict(base or {})
    out["usage"] = metrics.usage_dict()
    out["inputs"] = metrics.inputs_dict()
    out["models"] = metrics.models_dict()
    total_ms = None
    if metrics.build_started_perf is not None:
        total_ms = int((time.perf_counter() - metrics.build_started_perf) * 1000)
    out["timings"] = {
        "total_ms": total_ms,
        "stages_ms": dict(metrics.stage_timings_ms),
    }
    return out


# ---------------------------------------------------------------------------
# Blob fetcher
# ---------------------------------------------------------------------------

def _collect_workspace_sources(workspace_id: str):
    """Fetch active KB files for a workspace from Vercel Blob.

    Returns two lists of ``(filename, bytes)`` tuples, partitioned by
    kb_source. Every file row must carry a ``blob_url`` — rows without one
    are skipped with a warning since local disk is no longer an option.

    Runs from the build THREAD (``asyncio.to_thread`` in the worker), so
    async SQLAlchemy work is scheduled onto the event loop via
    ``run_coroutine_threadsafe`` (using ``asyncio.run`` here would spin up
    a new loop whose Futures don't match the engine's loop).
    """
    from sqlalchemy import select
    from src.infra.blob_loader import download_bytes_by_url
    from src.infra.db import get_main_loop, get_session
    from src.infra.db_models import WorkspaceFile

    async def _fetch():
        async with get_session() as s:
            r = await s.execute(
                select(WorkspaceFile)
                .where(WorkspaceFile.workspace_id == uuid.UUID(workspace_id))
                .where(WorkspaceFile.active.is_(True))
                .order_by(WorkspaceFile.created_at)
            )
            return r.scalars().all()

    loop = get_main_loop()
    if loop is None:
        raise RuntimeError(
            "Main event loop is not captured; cannot fetch workspace files from build thread."
        )
    fut = asyncio.run_coroutine_threadsafe(_fetch(), loop)
    rows = fut.result(timeout=30)

    knowledge_sources: list[tuple[str, bytes]] = []
    tool_sources: list[tuple[str, bytes]] = []
    for r in rows:
        if not r.blob_url:
            logger.warning(
                "WorkspaceFile id=%s (%s/%s) has no blob_url; skipping.",
                r.id, r.kb_source, r.filename,
            )
            continue
        try:
            data = download_bytes_by_url(r.blob_url)
        except Exception as exc:
            logger.error(
                "Blob download failed for ws=%s file=%s (%s): %s",
                workspace_id, r.filename, r.blob_url, exc,
            )
            raise RuntimeError(
                f"Failed to fetch KB file {r.filename!r} from Vercel Blob: {exc}"
            )
        if r.kb_source == "knowledge":
            knowledge_sources.append((r.filename, data))
        elif r.kb_source == "tool":
            tool_sources.append((r.filename, data))
    return knowledge_sources, tool_sources
