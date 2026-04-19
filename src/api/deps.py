"""
Shared FastAPI dependencies.

- ``require_workspace_id`` extracts X-Workspace-Id from the request, validates
  it exists in Neon for the anonymous user (Phase A) or current user
  (Phase B), sets the ``workspace_context`` contextvar so deeply-nested code
  in the graph pipeline can read it, and returns the id.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import Header, HTTPException

from src.config import USE_NEON
from src.workspace_context import set_current_workspace

logger = logging.getLogger(__name__)


async def require_workspace_id(
    x_workspace_id: str | None = Header(default=None, alias="X-Workspace-Id"),
) -> str:
    """Validate and install the active workspace for this request.

    Raises:
        400 if header is missing or malformed.
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

    # Verify the workspace exists
    from sqlalchemy import select
    from src.infra.db import get_session
    from src.infra.db_models import Workspace
    async with get_session() as s:
        r = await s.execute(select(Workspace).where(Workspace.id == uuid.UUID(x_workspace_id)))
        if r.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail=f"Workspace {x_workspace_id} not found.")

    set_current_workspace(x_workspace_id)
    return x_workspace_id
