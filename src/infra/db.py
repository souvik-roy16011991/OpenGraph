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
    - Strips libpq-only query params (sslmode, channel_binding, pgbouncer)
      that asyncpg doesn't understand.
    """
    if not url:
        return url
    out = url
    if out.startswith("postgres://"):
        out = "postgresql+asyncpg://" + out[len("postgres://"):]
    elif out.startswith("postgresql://"):
        out = "postgresql+asyncpg://" + out[len("postgresql://"):]
    for libpq_only in ("sslmode", "channel_binding", "pgbouncer"):
        if f"{libpq_only}=" in out:
            out = _strip_query_param(out, libpq_only)
    return out


def _strip_query_param(url: str, key: str) -> str:
    # Minimal, dependency-free query-string filter.
    if "?" not in url:
        return url
    base, qs = url.split("?", 1)
    kept = [p for p in qs.split("&") if not p.startswith(f"{key}=")]
    return base + ("?" + "&".join(kept) if kept else "")


def _is_transaction_pooler(url: str) -> bool:
    """Transaction-mode PgBouncer rewrites sessions per-transaction, which
    breaks asyncpg's prepared-statement cache. Neon's pooler defaults to
    session mode (safe), but expose the check so other poolers (e.g. a
    raw PgBouncer sidecar) can be detected by port :6543 or pgbouncer=true.
    """
    if not url:
        return False
    return ":6543" in url or "pgbouncer=true" in url


def _init_engine():
    global _engine, _session_maker
    if not USE_NEON:
        return
    if _engine is not None:
        return

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = _normalize_database_url(DATABASE_URL)
    # Neon requires SSL. asyncpg honors ssl='require' for the default OpenSSL
    # context; match by host to keep local postgres (no SSL) working.
    _needs_ssl = "neon.tech" in url or "sslmode=" in DATABASE_URL
    connect_args: dict = {"ssl": "require"} if _needs_ssl else {}

    if _is_transaction_pooler(DATABASE_URL):
        # asyncpg silently caches prepared statements per-connection; when the
        # pooler rewrites transactions onto different backends the cached
        # name becomes invalid on the next call. Disable the cache to survive.
        connect_args["statement_cache_size"] = 0
        connect_args["prepared_statement_cache_size"] = 0
        logger.warning(
            "Transaction-pooler URL detected (port 6543 / pgbouncer=true) — "
            "disabling asyncpg prepared-statement cache."
        )

    # Per-process pool sized for horizontal fleet safety. At N API workers
    # × M instances × pool_size=2 (+ max_overflow=10 headroom) we stay well
    # under Neon's connection ceiling (Launch plan allows ~901 direct,
    # thousands via pooler). Worker processes use the same per-process
    # pool; one build runs concurrently per worker.
    #
    # Tunable via env vars so operators can drop pool_size to 1 when
    # running many instances without a code deploy:
    #   DB_POOL_SIZE=2         (steady-state connections per process)
    #   DB_MAX_OVERFLOW=10     (burst headroom)
    #   DB_POOL_RECYCLE=1800   (seconds; Neon idle-kills at ~2h)
    #   DB_POOL_TIMEOUT=30     (seconds; fail fast when pool saturated)
    import os as _os
    pool_size = int(_os.environ.get("DB_POOL_SIZE", "2"))
    max_overflow = int(_os.environ.get("DB_MAX_OVERFLOW", "10"))
    pool_recycle = int(_os.environ.get("DB_POOL_RECYCLE", "1800"))
    pool_timeout = int(_os.environ.get("DB_POOL_TIMEOUT", "30"))

    _engine = create_async_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,          # drop dead connections (Neon idle-kills eventually)
        pool_recycle=pool_recycle,
        pool_timeout=pool_timeout,
        connect_args=connect_args,
    )
    _session_maker = async_sessionmaker(_engine, expire_on_commit=False)
    logger.info(
        "Neon async engine initialised for %s (ssl=%s, pool_size=%d, max_overflow=%d)",
        url.split("@")[-1].split("?")[0],
        bool(connect_args.get("ssl")),
        pool_size,
        max_overflow,
    )


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
        # --- Additive migrations for pre-auth DBs ---------------------------
        # create_all never alters an existing table, so add the JWT-auth
        # columns/indexes defensively on every startup. These are idempotent.
        from sqlalchemy import text
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(256)"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email ON users (email)"
        ))
        # --- GitHub OAuth columns (idempotent, nullable) ------------------
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS github_id VARCHAR(64)"
        ))
        await conn.execute(text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(512)"
        ))
        # Partial unique index — each GitHub account maps to at most one user,
        # but NULLs (email/password-only users) are excluded from the constraint.
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_users_github_id "
            "ON users (github_id) WHERE github_id IS NOT NULL"
        ))
        # --- build_jobs durable-queue additions ---------------------------
        # Support for the Postgres-as-queue build pipeline: workers write
        # ``heartbeat_at`` every 10s, claim rows via SELECT FOR UPDATE SKIP
        # LOCKED, and rely on the unique partial index to enforce per-
        # workspace single-flight. All idempotent.
        await conn.execute(text(
            "ALTER TABLE build_jobs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ"
        ))
        await conn.execute(text(
            "ALTER TABLE build_jobs ADD COLUMN IF NOT EXISTS worker_id TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE build_jobs ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 1"
        ))
        # Fast claim path — covers the worker's dequeue and the sweeper.
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_build_jobs_claim "
            "ON build_jobs (status, created_at) "
            "WHERE status IN ('queued','running')"
        ))
        # Enforce per-workspace single-flight: only one queued-or-running
        # row per workspace at a time. INSERT that violates → IntegrityError
        # → 409 from the enqueue endpoint. Replaces the old in-memory
        # ``_running_by_ws`` dict + threading.Lock.
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_build_jobs_active_per_ws "
            "ON build_jobs (workspace_id) "
            "WHERE status IN ('queued','running')"
        ))
        # --- workspaces: delete-saga columns ------------------------------
        # ``deleted_at`` IS NULL means live; any non-null value means the
        # cross-store cascade is in progress or has been retried. The HTTP
        # DELETE handler sets this column first; a background sweeper
        # hard-deletes the row once Memgraph + Qdrant + Blob all confirm
        # cleanup. All read paths filter for ``deleted_at IS NULL``.
        await conn.execute(text(
            "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ"
        ))
        await conn.execute(text(
            "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS "
            "deletion_failure_count INTEGER NOT NULL DEFAULT 0"
        ))
        await conn.execute(text(
            "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS deletion_last_error TEXT"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_workspaces_deleted_at "
            "ON workspaces (deleted_at) WHERE deleted_at IS NOT NULL"
        ))
        # --- billing_accounts: grandfather existing users -----------------
        # First-deploy protection: every ``users`` row that predates billing
        # would otherwise get a ``plan_tier='trial'`` row on first /me call
        # and immediately hit the 1-workspace / 1-build / 5-chat caps.
        # Seed pre-existing users at ``plan_tier='payg'`` instead so they
        # keep working. New accounts (created AFTER this migration ran)
        # still start on trial via the signup path's default.
        #
        # Raw SQL must provide values for every NOT NULL column explicitly
        # — SQLAlchemy's ``default=`` only applies through the ORM layer.
        # Safe to run on every startup: INSERT ... ON CONFLICT DO NOTHING
        # only fires for users without a billing row. Idempotent.
        await conn.execute(text("""
            INSERT INTO billing_accounts (
              user_id, plan_tier,
              trial_build_count, trial_chat_count,
              subscription_credits, topup_credits, overdraft_limit
            )
            SELECT id, 'payg', 0, 0, 0, 0, -200 FROM users
            ON CONFLICT (user_id) DO NOTHING
        """))
    logger.info("Neon tables ensured (users, workspaces, build_jobs, chat_*, kb_uploads, audit, billing_accounts).")


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
