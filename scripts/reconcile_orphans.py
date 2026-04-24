"""Find and optionally purge storage artifacts that Neon doesn't know about.

At 1M workspaces, silent divergence between Neon (source of truth for
metadata) and the side stores (Supabase Storage / Qdrant / Memgraph) will
leak storage + cost. This script surfaces every orphan in one pass:

    Supabase Storage: any object whose top-level key segment ``{ws_id}`` is
                      not the id of a live Neon workspace row.
    Qdrant:           any collection named ``kb-{wid-short}`` whose wid-short
                      doesn't resolve to a live Neon workspace (legacy mode),
                      OR in shared-collection mode, any ``workspace_id``
                      payload value present in the collection that isn't a
                      live row.
    Memgraph:         any DISTINCT workspace_id value across KBNode nodes
                      that isn't a live Neon workspace.

Dry run by default. Pass ``--delete`` to actually purge — the script will
refuse to delete more than 100 artifacts in a single invocation unless
``--force`` is also passed (guard against a misconfigured env wiping a
healthy cluster).

Usage:
    python -m scripts.reconcile_orphans                # dry run, all stores
    python -m scripts.reconcile_orphans --only blob    # only object storage
    python -m scripts.reconcile_orphans --delete       # purge (small sets)
    python -m scripts.reconcile_orphans --delete --force
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Iterable

logger = logging.getLogger("reconcile_orphans")

_DELETE_CAP = 100


async def _live_workspace_ids() -> set[str]:
    """Return the set of live (non-soft-deleted) workspace UUIDs from Neon."""
    from sqlalchemy import select
    from src.infra.db import get_session
    from src.infra.db_models import Workspace

    async with get_session() as s:
        rows = (await s.execute(
            select(Workspace.id).where(Workspace.deleted_at.is_(None))
        )).scalars().all()
    return {str(wid) for wid in rows}


def _short_wid(wid: str) -> str:
    # Mirror src.graph_builder.builder._short_wid so collection names line up.
    from src.graph_builder.builder import _short_wid as _impl
    return _impl(wid)


# ---------------------------------------------------------------------------
# Object-storage reconciliation (Supabase Storage, S3-compatible)
# ---------------------------------------------------------------------------

def _reconcile_blob(live_ids: set[str]) -> list[str]:
    """Walk the Supabase bucket, return orphaned object keys.

    Object-storage keys follow ``{workspace_id}/...``. An orphan is any
    key whose leading segment is not a live workspace id.
    """
    from src.config import USE_SUPABASE_STORAGE, SUPABASE_BUCKET
    if not USE_SUPABASE_STORAGE:
        logger.info("Storage: Supabase Storage not configured — skipping.")
        return []

    from src.infra.blob_loader import _s3_client  # type: ignore[attr-defined]
    s3 = _s3_client()

    orphans: list[str] = []
    seen = 0
    token = None
    while True:
        kwargs: dict = {"Bucket": SUPABASE_BUCKET, "MaxKeys": 1000}
        if token is not None:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []) or []:
            seen += 1
            key = obj["Key"]
            parts = key.split("/", 1)
            wid_candidate = parts[0] if parts else ""
            if wid_candidate and wid_candidate not in live_ids:
                orphans.append(key)
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
        if not token:
            break
    logger.info("Storage: scanned %d objects, %d orphans.", seen, len(orphans))
    return orphans


def _delete_blob_urls(keys: Iterable[str]) -> int:
    """Delete orphan object keys.

    Delegates to the adapter's ``_delete_keys_batched`` which uses
    sequential ``DeleteObject`` calls (Supabase S3 does not support the
    Multi-Object Delete operation).
    """
    from src.config import USE_SUPABASE_STORAGE
    if not USE_SUPABASE_STORAGE:
        return 0
    from src.infra.blob_loader import _delete_keys_batched  # type: ignore[attr-defined]
    return _delete_keys_batched(list(keys))


# ---------------------------------------------------------------------------
# Qdrant reconciliation
# ---------------------------------------------------------------------------

def _reconcile_qdrant(live_ids: set[str]) -> tuple[list[str], list[str]]:
    """Return (orphan_collections, orphan_workspace_ids_in_shared_collection)."""
    from src.config import (
        QDRANT_API_KEY,
        QDRANT_SHARED_COLLECTION,
        QDRANT_URL,
    )
    if not (QDRANT_URL and QDRANT_API_KEY):
        logger.info("Qdrant: URL/API_KEY not set — skipping.")
        return [], []

    from qdrant_client import QdrantClient
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60)

    live_shorts = {_short_wid(wid) for wid in live_ids}

    orphan_collections: list[str] = []
    orphan_ws_ids_shared: list[str] = []

    for c in client.get_collections().collections:
        name = c.name
        if name.startswith("kb-") and name != QDRANT_SHARED_COLLECTION:
            short = name[len("kb-"):]
            if short not in live_shorts:
                orphan_collections.append(name)

    if QDRANT_SHARED_COLLECTION:
        # Scroll the shared collection, collect distinct workspace_ids seen
        # in payloads, and diff against live_ids. Bounded work — we stop
        # after seeing each distinct wid once.
        seen_ws: set[str] = set()
        next_page = None
        while True:
            points, next_page = client.scroll(
                collection_name=QDRANT_SHARED_COLLECTION,
                limit=1000,
                with_payload=True,
                with_vectors=False,
                offset=next_page,
            )
            for p in points:
                ws = (p.payload or {}).get("workspace_id")
                if ws:
                    seen_ws.add(ws)
            if next_page is None:
                break
        orphan_ws_ids_shared = sorted(seen_ws - live_ids)

    logger.info(
        "Qdrant: %d orphan collections, %d orphan workspace_ids in shared collection.",
        len(orphan_collections), len(orphan_ws_ids_shared),
    )
    return orphan_collections, orphan_ws_ids_shared


def _delete_qdrant_collections(names: Iterable[str]) -> int:
    from src.config import QDRANT_API_KEY, QDRANT_URL
    from qdrant_client import QdrantClient
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60)
    count = 0
    for name in names:
        try:
            client.delete_collection(name)
            count += 1
        except Exception as exc:
            logger.warning("Qdrant delete_collection %s failed: %s", name, exc)
    return count


def _delete_qdrant_shared_ws(ws_ids: Iterable[str]) -> int:
    from src.config import QDRANT_API_KEY, QDRANT_SHARED_COLLECTION, QDRANT_URL
    from qdrant_client import QdrantClient
    from qdrant_client.models import (
        FieldCondition,
        Filter,
        FilterSelector,
        MatchValue,
    )
    client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60)
    count = 0
    for wid in ws_ids:
        flt = Filter(
            must=[FieldCondition(key="workspace_id", match=MatchValue(value=wid))]
        )
        try:
            client.delete(
                collection_name=QDRANT_SHARED_COLLECTION,
                points_selector=FilterSelector(filter=flt),
                wait=True,
            )
            count += 1
        except Exception as exc:
            logger.warning("Qdrant filter-delete ws=%s failed: %s", wid, exc)
    return count


# ---------------------------------------------------------------------------
# Memgraph reconciliation
# ---------------------------------------------------------------------------

def _reconcile_memgraph(live_ids: set[str]) -> list[str]:
    from src.config import USE_MEMGRAPH, USE_NEO4J
    if not (USE_MEMGRAPH or USE_NEO4J):
        logger.info("Memgraph/Neo4j: no graph backend configured — skipping.")
        return []

    from src.config import MEMGRAPH_PASSWORD, MEMGRAPH_URI, MEMGRAPH_USERNAME
    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(
        MEMGRAPH_URI, auth=(MEMGRAPH_USERNAME, MEMGRAPH_PASSWORD)
    )
    try:
        with driver.session() as s:
            result = s.run(
                "MATCH (n:KBNode) RETURN DISTINCT n.workspace_id AS wid"
            )
            all_ws = {r["wid"] for r in result if r.get("wid")}
    finally:
        driver.close()
    orphans = sorted(all_ws - live_ids)
    logger.info("Memgraph: scanned %d workspace ids, %d orphans.", len(all_ws), len(orphans))
    return orphans


def _delete_memgraph_workspace(ws_id: str) -> None:
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
    store.clear_workspace(ws_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> int:
    p = argparse.ArgumentParser(description="Reconcile side-store orphans against Neon.")
    p.add_argument("--only", choices=["blob", "qdrant", "memgraph"], help="limit to one store")
    p.add_argument("--delete", action="store_true", help="actually purge orphans (dry-run by default)")
    p.add_argument("--force", action="store_true", help="allow --delete to purge >100 artifacts")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    live = await _live_workspace_ids()
    logger.info("Neon: %d live workspaces.", len(live))

    report: dict[str, int] = {}

    if args.only in (None, "blob"):
        blob_orphans = _reconcile_blob(live)
        report["blob_orphans"] = len(blob_orphans)
        if args.delete and blob_orphans:
            if len(blob_orphans) > _DELETE_CAP and not args.force:
                logger.warning(
                    "Storage: refusing to delete %d orphans without --force "
                    "(cap=%d).", len(blob_orphans), _DELETE_CAP,
                )
            else:
                n = _delete_blob_urls(blob_orphans)
                logger.info("Storage: purged %d orphan objects.", n)

    if args.only in (None, "qdrant"):
        qdrant_cols, qdrant_ws = _reconcile_qdrant(live)
        report["qdrant_orphan_collections"] = len(qdrant_cols)
        report["qdrant_orphan_workspace_ids_in_shared"] = len(qdrant_ws)
        if args.delete:
            if qdrant_cols:
                if len(qdrant_cols) > _DELETE_CAP and not args.force:
                    logger.warning(
                        "Qdrant collections: refusing to delete %d without --force.",
                        len(qdrant_cols),
                    )
                else:
                    n = _delete_qdrant_collections(qdrant_cols)
                    logger.info("Qdrant: purged %d orphan collections.", n)
            if qdrant_ws:
                if len(qdrant_ws) > _DELETE_CAP and not args.force:
                    logger.warning(
                        "Qdrant shared-collection ws: refusing to delete %d "
                        "without --force.", len(qdrant_ws),
                    )
                else:
                    n = _delete_qdrant_shared_ws(qdrant_ws)
                    logger.info("Qdrant: filter-deleted %d orphan workspaces.", n)

    if args.only in (None, "memgraph"):
        mg_orphans = _reconcile_memgraph(live)
        report["memgraph_orphan_workspaces"] = len(mg_orphans)
        if args.delete and mg_orphans:
            if len(mg_orphans) > _DELETE_CAP and not args.force:
                logger.warning(
                    "Memgraph: refusing to delete %d orphans without --force.",
                    len(mg_orphans),
                )
            else:
                for wid in mg_orphans:
                    try:
                        _delete_memgraph_workspace(wid)
                    except Exception as exc:
                        logger.warning("Memgraph clear ws=%s failed: %s", wid, exc)
                logger.info("Memgraph: cleared %d orphan workspaces.", len(mg_orphans))

    print("RECONCILE REPORT:")
    for k, v in report.items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
