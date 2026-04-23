"""Alembic environment.

Reads DATABASE_URL from ``src.config`` so operators don't have to duplicate
secrets between ``.env`` and ``alembic.ini``. Normalises any ``postgres://``
scheme to psycopg-driver form at migration time; the runtime engine still
uses asyncpg (see ``src/infra/db.py``), but Alembic defaults to the sync
driver so autogenerate can introspect the database schema cheaply.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

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


def _sync_url(url: str) -> str:
    """Rewrite an async-driver URL to the sync psycopg driver for Alembic.

    Alembic's autogenerate path expects a sync engine; runtime async is
    handled elsewhere. Strip libpq-only params that asyncpg didn't like but
    psycopg handles fine (sslmode etc.) — leave them in the URL.
    """
    if not url:
        return url
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://"):]
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


_RESOLVED_URL = _sync_url(DATABASE_URL)
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


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
