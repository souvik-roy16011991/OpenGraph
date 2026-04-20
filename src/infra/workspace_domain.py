"""
Per-workspace DomainProfile resolver.

The app used to hold a single process-global ``KBConfig`` singleton
(``src.kb_config._ACTIVE``). In a multi-tenant deploy this is a correctness
bug — user A's ``PUT /config/domain`` mutates the same object user B reads.

This module is the bridge: given a workspace_id, return the workspace's
DomainProfile overrides (stored as a JSONB dict on ``Workspace.domain_config``)
so ``get_active_kb_config()`` can overlay them on the disk seed.

Two access modes:
- Async (from an HTTP request handler): ``get_workspace_domain()``.
- Sync (from the build thread or any non-async call site): ``get_workspace_domain_sync()``
  which schedules onto the main event loop via ``run_coroutine_threadsafe`` —
  same pattern as ``src.infra.workspace_llm.get_workspace_llm_model_sync``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select

from src.config import USE_NEON
from src.infra.db import get_session
from src.infra.db_models import Workspace

logger = logging.getLogger(__name__)


async def get_workspace_domain(workspace_id: str) -> Optional[dict[str, Any]]:
    """Return the workspace's ``domain_config`` dict, or None when unset / Neon off."""
    if not USE_NEON:
        return None
    try:
        wid = uuid.UUID(workspace_id)
    except (ValueError, TypeError):
        return None
    async with get_session() as s:
        ws = (await s.execute(select(Workspace).where(Workspace.id == wid))).scalar_one_or_none()
        if ws is None or not ws.domain_config:
            return None
        return dict(ws.domain_config)


async def set_workspace_domain(workspace_id: str, domain: dict[str, Any]) -> None:
    """Persist the DomainProfile override fields on the workspace row."""
    if not USE_NEON:
        raise RuntimeError("DATABASE_URL (Neon) is required to persist domain config.")
    wid = uuid.UUID(workspace_id)
    async with get_session() as s:
        ws = (await s.execute(select(Workspace).where(Workspace.id == wid))).scalar_one_or_none()
        if ws is None:
            raise LookupError(f"Workspace {workspace_id} not found.")
        current = dict(ws.domain_config or {})
        current.update(domain)
        # Reassign to trigger SQLAlchemy's JSONB dirty-tracking.
        ws.domain_config = current
        await s.commit()


def get_workspace_domain_sync(workspace_id: str) -> Optional[dict[str, Any]]:
    """Thread-safe synchronous read for call sites that can't await.

    Used by ``get_active_kb_config()`` in the build thread. Matches the
    pattern established by ``src.infra.workspace_llm.get_workspace_llm_model_sync``.
    """
    import asyncio
    from src.infra.db import get_main_loop

    loop = get_main_loop()
    if loop is None:
        return None
    try:
        fut = asyncio.run_coroutine_threadsafe(get_workspace_domain(workspace_id), loop)
        return fut.result(timeout=10)
    except Exception as exc:
        logger.warning("Sync workspace domain lookup failed for ws=%s: %s", workspace_id, exc)
        return None
