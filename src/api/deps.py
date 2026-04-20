"""
Shared FastAPI dependencies.

- ``require_user`` resolves the caller's Neon Auth identity (raises 401 if
  no/invalid token, 503 if auth isn't configured). Re-exported from
  ``src.api.auth`` for convenience so route modules have a single import.
- ``require_workspace_id`` extracts X-Workspace-Id, validates it, **verifies
  ownership against the caller's User row**, loads the workspace's
  ``domain_config`` into the per-workspace cache, installs the
  ``workspace_context`` contextvar, and returns the id.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import Depends, Header, HTTPException

from src.api.auth import require_user  # re-export so callers can `from src.api.deps import require_user`
from src.config import USE_NEON
from src.infra.db_models import User
from src.workspace_context import set_current_workspace

__all__ = ["require_user", "require_workspace_id"]

logger = logging.getLogger(__name__)


async def require_workspace_id(
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-Id"),
    user: User = Depends(require_user),
) -> str:
    """Validate workspace header, verify ownership, install context.

    Raises:
        400 if header is missing or malformed.
        401 if the caller is not authenticated (bubbled up from require_user).
        403 if the workspace exists but isn't owned by the caller.
        404 if the workspace id doesn't exist in Neon.
        503 if Neon is not configured.
    """
    if not x_workspace_id:
        raise HTTPException(status_code=400, detail="Missing X-Workspace-Id header.")
    try:
        uuid.UUID(x_workspace_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="X-Workspace-Id must be a UUID.")

    if not USE_NEON:
        raise HTTPException(
            status_code=503,
            detail="Neon (DATABASE_URL) is required for workspace-scoped operations.",
        )

    # Load the workspace row AND the domain override in one round-trip.
    from sqlalchemy import select
    from src.infra.db import get_session
    from src.infra.db_models import Workspace
    async with get_session() as s:
        ws = (await s.execute(
            select(Workspace).where(Workspace.id == uuid.UUID(x_workspace_id))
        )).scalar_one_or_none()
        if ws is None:
            # 404 (not 403) even though the caller isn't the owner: leaking
            # "exists but not yours" is strictly worse than leaking "doesn't
            # exist". This matches how e.g. GitHub responds to foreign repo ids.
            raise HTTPException(status_code=404, detail=f"Workspace {x_workspace_id} not found.")
        if ws.user_id != user.id:
            # Intentionally 404 to avoid existence leakage across tenants.
            raise HTTPException(status_code=404, detail=f"Workspace {x_workspace_id} not found.")
        domain_override = dict(ws.domain_config) if ws.domain_config else None

    # Only AFTER ownership passes: install the context + prime the cache.
    # Setting the contextvar before the ownership check would risk leaking
    # the workspace id in log lines emitted by the 403 path.
    set_current_workspace(x_workspace_id)

    from src.kb_config import prime_workspace_kb_config
    prime_workspace_kb_config(x_workspace_id, domain_override)

    return x_workspace_id
