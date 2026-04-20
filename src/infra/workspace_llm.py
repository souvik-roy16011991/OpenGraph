"""
Workspace-scoped LLM preference resolver.

The selected model is persisted under ``Workspace.graph_config["llm_model"]``
(JSONB, no DB migration required). Helpers here are the single path the rest
of the codebase uses to read/write that value, so callers never hand-craft
the JSONB merge logic.

Resolution chain for the chat pipeline:
    request.llm_model  >  workspace.graph_config["llm_model"]  >  env LLM_MODEL

The env default is applied downstream in ``src.agent.nodes._get_llm`` — this
module only answers "what's the workspace default?".
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from sqlalchemy import select

from src.config import USE_NEON
from src.infra.db import get_session
from src.infra.db_models import Workspace

logger = logging.getLogger(__name__)

_LLM_KEY = "llm_model"


async def get_workspace_llm_model(workspace_id: str) -> Optional[str]:
    """Return the workspace's chosen model, or None when unset / Neon off."""
    if not USE_NEON:
        return None
    try:
        wid = uuid.UUID(workspace_id)
    except (ValueError, TypeError):
        return None
    async with get_session() as s:
        ws = (await s.execute(select(Workspace).where(Workspace.id == wid))).scalar_one_or_none()
        if ws is None or not ws.graph_config:
            return None
        model = ws.graph_config.get(_LLM_KEY)
        return model if isinstance(model, str) and model else None


async def set_workspace_llm_model(workspace_id: str, model: Optional[str]) -> None:
    """Persist (or clear) the workspace's chosen model.

    Passing None / empty string clears the preference; subsequent reads will
    return None and the resolution chain falls through to the env default.
    """
    if not USE_NEON:
        raise RuntimeError("DATABASE_URL (Neon) is required to persist LLM selection.")
    wid = uuid.UUID(workspace_id)
    async with get_session() as s:
        ws = (await s.execute(select(Workspace).where(Workspace.id == wid))).scalar_one_or_none()
        if ws is None:
            raise LookupError(f"Workspace {workspace_id} not found.")
        current = dict(ws.graph_config or {})
        if model:
            current[_LLM_KEY] = model
        else:
            current.pop(_LLM_KEY, None)
        # Reassign to trigger SQLAlchemy's JSONB dirty-tracking.
        ws.graph_config = current
        await s.commit()


def get_workspace_llm_model_sync(workspace_id: str) -> Optional[str]:
    """Thread-safe synchronous read.

    Needed from the build job thread which can't await. Uses the
    ``run_coroutine_threadsafe`` pattern already established for
    ``_collect_workspace_sources``.
    """
    import asyncio
    from src.infra.db import get_main_loop

    loop = get_main_loop()
    if loop is None:
        return None
    try:
        fut = asyncio.run_coroutine_threadsafe(get_workspace_llm_model(workspace_id), loop)
        return fut.result(timeout=10)
    except Exception as exc:
        logger.warning("Sync workspace LLM lookup failed for ws=%s: %s", workspace_id, exc)
        return None
