"""
One-shot wipe: clear every backing store so we can start clean with the
multi-workspace schema. Runs Memgraph, Qdrant, Supabase Storage, and Neon
in sequence. Idempotent — safe to re-run.

Usage:  python3 scripts/wipe_all.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("wipe")


def wipe_memgraph() -> None:
    from src.config import MEMGRAPH_DATABASE, MEMGRAPH_PASSWORD, MEMGRAPH_URI, MEMGRAPH_USERNAME, USE_MEMGRAPH
    if not USE_MEMGRAPH:
        logger.info("Memgraph disabled; skipping.")
        return
    try:
        from src.infra.memgraph_store import MemgraphGraphStore
        store = MemgraphGraphStore(MEMGRAPH_URI, MEMGRAPH_USERNAME, MEMGRAPH_PASSWORD, MEMGRAPH_DATABASE)
        store.clear_graph()
        logger.info("Memgraph wiped.")
    except Exception as exc:
        logger.warning("Memgraph wipe failed: %s", exc)


def wipe_qdrant() -> None:
    from src.config import QDRANT_API_KEY, QDRANT_URL, USE_QDRANT
    if not USE_QDRANT:
        logger.info("Qdrant disabled; skipping.")
        return
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60)
        for c in client.get_collections().collections:
            client.delete_collection(c.name)
            logger.info("Dropped Qdrant collection: %s", c.name)
    except Exception as exc:
        logger.warning("Qdrant wipe failed: %s", exc)


def wipe_blob() -> None:
    from src.config import SUPABASE_BUCKET, USE_SUPABASE_STORAGE
    if not USE_SUPABASE_STORAGE:
        logger.info("Supabase Storage disabled; skipping.")
        return
    try:
        from src.infra.blob_loader import _delete_keys_batched, _s3_client  # type: ignore[attr-defined]
        s3 = _s3_client()

        # Walk the whole bucket and collect every key.
        all_keys: list[str] = []
        token = None
        while True:
            kwargs: dict = {"Bucket": SUPABASE_BUCKET, "MaxKeys": 1000}
            if token is not None:
                kwargs["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kwargs)
            for obj in resp.get("Contents", []) or []:
                all_keys.append(obj["Key"])
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
            if not token:
                break

        if not all_keys:
            logger.info("No Supabase Storage objects to wipe.")
            return

        # Supabase S3 doesn't support Multi-Object Delete; the adapter
        # falls back to sequential DeleteObject.
        n = _delete_keys_batched(all_keys)
        logger.info("Deleted %d/%d objects from Supabase Storage.", n, len(all_keys))
    except Exception as exc:
        logger.warning("Supabase Storage wipe failed: %s", exc)


async def wipe_neon() -> None:
    from src.config import USE_NEON
    if not USE_NEON:
        logger.info("Neon disabled; skipping.")
        return
    try:
        from sqlalchemy import text
        from src.infra.db import get_session
        async with get_session() as s:
            # Drop tables; they'll be recreated on next server startup with new schema.
            for t in [
                "chat_messages", "chat_sessions",
                "config_versions", "kb_uploads", "build_jobs",
                "workspace_files", "workspaces", "users",
            ]:
                try:
                    await s.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
                except Exception as exc:
                    logger.warning("DROP TABLE %s failed: %s", t, exc)
            await s.commit()
        logger.info("Neon tables dropped.")
    except Exception as exc:
        logger.warning("Neon wipe failed: %s", exc)


async def main() -> None:
    wipe_memgraph()
    wipe_qdrant()
    wipe_blob()
    await wipe_neon()
    logger.info("All-wipe complete.")


if __name__ == "__main__":
    asyncio.run(main())
