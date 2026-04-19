"""
One-shot wipe: clear every backing store so we can start clean with the
multi-workspace schema. Runs Memgraph, Qdrant, Vercel Blob, local /data,
and Neon in sequence. Idempotent — safe to re-run.

Usage:  python3 scripts/wipe_all.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("wipe")


def wipe_local_data() -> None:
    from src.config import DATA_DIR
    if not Path(DATA_DIR).exists():
        logger.info("Local /data absent; nothing to wipe.")
        return
    for p in Path(DATA_DIR).iterdir():
        if p.is_dir():
            shutil.rmtree(p)
        else:
            try:
                p.unlink()
            except OSError:
                pass
    logger.info("Wiped local /data contents.")


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
    from src.config import BLOB_READ_WRITE_TOKEN, BLOB_STORE_PATH, USE_BLOB_STORAGE
    if not USE_BLOB_STORAGE:
        logger.info("Vercel Blob disabled; skipping.")
        return
    try:
        import httpx
        base = "https://blob.vercel-storage.com"
        headers = {"Authorization": f"Bearer {BLOB_READ_WRITE_TOKEN}"}
        # List all blobs under the prefix
        r = httpx.get(f"{base}?prefix={BLOB_STORE_PATH}/", headers=headers, timeout=30)
        r.raise_for_status()
        blobs = r.json().get("blobs", [])
        if not blobs:
            logger.info("No Vercel Blob objects to wipe.")
            return
        # Batch delete
        urls = [b.get("url") for b in blobs if b.get("url")]
        if urls:
            rr = httpx.post(f"{base}/delete", json={"urls": urls}, headers={**headers, "Content-Type": "application/json"}, timeout=60)
            rr.raise_for_status()
            logger.info("Deleted %d blobs from Vercel Blob.", len(urls))
    except Exception as exc:
        logger.warning("Vercel Blob wipe failed: %s", exc)


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
    wipe_local_data()
    wipe_memgraph()
    wipe_qdrant()
    wipe_blob()
    await wipe_neon()
    logger.info("All-wipe complete.")


if __name__ == "__main__":
    asyncio.run(main())
