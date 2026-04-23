"""Alembic environment.

Reads DATABASE_URL from ``src.config`` so operators don't have to duplicate
secrets between ``.env`` and ``alembic.ini``. Runs migrations via the same
asyncpg driver the runtime uses — no second Postgres DBAPI dependency.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make ``src`` importable when alembic is invoked from repo root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.config import DATABASE_URL  # noqa: E402
from src.infra.db_models import Base  # noqa: E402 — registers models on Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _async_url(url: str) -> str:
    """Ensure the URL uses the asyncpg driver that the runtime engine uses."""
    if not url:
        return url
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://"):]
    return url


_RESOLVED_URL = _async_url(DATABASE_URL)
if _RESOLVED_URL:
    config.set_main_option("sqlalchemy.url", _RESOLVED_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode against a URL, not a live engine."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online_async() -> None:
    """Run migrations in 'online' mode using an asyncpg-backed engine."""
    # asyncpg doesn't understand libpq-only query params.
    import re
    cfg = config.get_section(config.config_ini_section, {})
    url = cfg.get("sqlalchemy.url", "")
    for key in ("sslmode", "channel_binding", "pgbouncer"):
        url = re.sub(rf"[?&]{key}=[^&]*", "", url)
        if url.endswith("?"):
            url = url[:-1]
    cfg["sqlalchemy.url"] = url
    # Neon requires SSL — match the runtime engine's hint.
    connect_args = {"ssl": "require"} if "neon.tech" in url else {}

    connectable = async_engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_migrations_online_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
