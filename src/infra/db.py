"""
Neon Postgres async engine + session factory.

Usage:
  - In async routes:
        async with get_session() as s:
            s.add(row)
            await s.commit()
  - From sync/background threads (e.g. build_jobs thread):
        await_in_loop(upsert_build_job(job))
    which schedules the coroutine on the main event loop via
    run_coroutine_threadsafe.

Safe fallback: if DATABASE_URL is not set, USE_NEON=False, and all writes are
wrapped by callers in `if USE_NEON:` so nothing runs. This module still imports
cleanly in that case; the engine/session objects are simply None.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from src.config import DATABASE_URL, USE_NEON

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Engine + session
# ---------------------------------------------------------------------------

_engine = None
_session_maker = None
_main_loop: Optional[asyncio.AbstractEventLoop] = None


def _normalize_database_url(url: str) -> str:
    """Rewrite the URL so it is an asyncpg-compatible SQLAlchemy URL.

    - postgres://           -> postgresql+asyncpg://
    - postgresql://         -> postgresql+asyncpg://
    - Remove sslmode=...    (asyncpg takes ssl via connect_args, not URL)
    """
    if not url:
        return url
    out = url
    if out.startswith("postgres://"):
        out = "postgresql+asyncpg://" + out[len("postgres://"):]
    elif out.startswith("postgresql://"):
        out = "postgresql+asyncpg://" + out[len("postgresql://"):]
    # sslmode= is libpq syntax; asyncpg uses `ssl=` kwarg.  Strip and remember
    # whether SSL was requested.
    if "sslmode=" in out:
        out = _strip_query_param(out, "sslmode")
    # channel_binding= is another libpq-ism asyncpg doesn't understand
    if "channel_binding=" in out:
        out = _strip_query_param(out, "channel_binding")
    return out


def _strip_query_param(url: str, key: str) -> str:
    # Minimal, dependency-free query-string filter.
    if "?" not in url:
        return url
    base, qs = url.split("?", 1)
    kept = [p for p in qs.split("&") if not p.startswith(f"{key}=")]
    return base + ("?" + "&".join(kept) if kept else "")


def _init_engine():
    global _engine, _session_maker
    if not USE_NEON:
        return
    if _engine is not None:
        return

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = _normalize_database_url(DATABASE_URL)
    # Neon requires SSL.  asyncpg honors ssl=True for OpenSSL default context.
    connect_args = {"ssl": "require"} if "neon.tech" in url or "sslmode=" in DATABASE_URL else {}
    _engine = create_async_engine(
        url,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=connect_args,
    )
    _session_maker = async_sessionmaker(_engine, expire_on_commit=False)
    logger.info("Neon async engine initialised for %s", url.split("@")[-1].split("?")[0])


@asynccontextmanager
async def get_session() -> AsyncIterator:
    """Yield an AsyncSession. Raises if USE_NEON=False (callers should guard)."""
    _init_engine()
    if _session_maker is None:
        raise RuntimeError("Neon is not configured (DATABASE_URL empty).")
    async with _session_maker() as session:
        yield session


async def init_db() -> None:
    """Create all tables if they don't exist. No-op if USE_NEON=False."""
    if not USE_NEON:
        logger.info("Neon disabled (DATABASE_URL empty) — skipping table creation.")
        return
    _init_engine()
    # Import models here so SQLAlchemy registers them on Base.metadata before create_all.
    from src.infra import db_models  # noqa: F401
    from src.infra.db_models import Base

    assert _engine is not None
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Neon tables ensured (build_jobs, chat_sessions, chat_messages, config_versions, kb_uploads).")


# ---------------------------------------------------------------------------
# Sync -> async bridge for background-thread writes
# ---------------------------------------------------------------------------

def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Capture the main event loop so background threads can schedule coros on it."""
    global _main_loop
    _main_loop = loop


def get_main_loop() -> Optional[asyncio.AbstractEventLoop]:
    return _main_loop


def fire_and_forget(coro) -> None:
    """Schedule *coro* on the main event loop from any thread; ignore the result.

    If no main loop is captured yet (very early startup, or tests), log and drop.
    """
    loop = _main_loop
    if loop is None:
        logger.debug("fire_and_forget: main loop not set yet; dropping write.")
        try:
            coro.close()
        except Exception:
            pass
        return
    try:
        asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as exc:
        logger.warning("fire_and_forget scheduling failed: %s", exc)
        try:
            coro.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Startup housekeeping — mark orphaned running jobs
# ---------------------------------------------------------------------------

async def mark_orphaned_running_jobs() -> int:
    """After a restart, any job still marked 'running' is dead. Flip to 'error'."""
    if not USE_NEON:
        return 0
    from sqlalchemy import update
    from src.infra.db_models import BuildJobRow

    async with get_session() as s:
        result = await s.execute(
            update(BuildJobRow)
            .where(BuildJobRow.status.in_(["queued", "running"]))
            .values(status="error", error="orphaned — process restarted before completion")
            .returning(BuildJobRow.job_id)
        )
        orphans = result.fetchall()
        await s.commit()
        if orphans:
            logger.warning("Marked %d orphaned build job(s) as error.", len(orphans))
        return len(orphans)
