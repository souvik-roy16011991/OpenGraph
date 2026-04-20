"""
Current-user endpoints.

    GET    /api/v1/me          Profile + rolled-up counts
    PATCH  /api/v1/me          Update display_name
    GET    /api/v1/me/audit    Paginated user audit trail

Auth (signup / login / logout) lives in ``auth_routes``. This module reads
the authenticated user via ``require_user`` and only touches profile fields.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.api.auth import invalidate_user_cache, require_user
from src.config import USE_NEON
from src.infra.audit import record_audit
from src.infra.db import get_session
from src.infra.db_models import (
    BuildJobRow,
    ChatMessage,
    ChatSession,
    User,
    UserAuditLog,
    Workspace,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class MeResponse(BaseModel):
    id: str
    email: Optional[str]
    display_name: Optional[str]
    created_at: str
    auth_mode: str  # always 'jwt' — retained for API-client compatibility
    workspace_count: int
    build_count: int
    chat_count: int


class UpdateMeRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=256)


class AuditEntry(BaseModel):
    id: int
    action: str
    target_type: Optional[str]
    target_id: Optional[str]
    workspace_id: Optional[str]
    metadata: Optional[dict[str, Any]]
    created_at: str


class AuditListResponse(BaseModel):
    entries: list[AuditEntry]
    has_more: bool
    next_before_id: Optional[int]  # cursor for paging


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_neon() -> None:
    if not USE_NEON:
        raise HTTPException(status_code=503, detail="DATABASE_URL (Neon) is required.")


async def _rollup_counts(user_id) -> dict[str, int]:
    """Return per-user aggregate counts used by the profile card."""
    async with get_session() as s:
        ws_ids_rows = (await s.execute(
            select(Workspace.id).where(Workspace.user_id == user_id)
        )).all()
        ws_ids = [r[0] for r in ws_ids_rows]
        if not ws_ids:
            return {"workspaces": 0, "builds": 0, "chats": 0}
        builds = int((await s.execute(
            select(func.count()).select_from(BuildJobRow).where(BuildJobRow.workspace_id.in_(ws_ids))
        )).scalar_one())
        # chats = total assistant-rendered messages across sessions in these workspaces
        chats = int((await s.execute(
            select(func.count())
            .select_from(ChatMessage)
            .join(ChatSession, ChatSession.session_id == ChatMessage.session_id)
            .where(ChatSession.workspace_id.in_(ws_ids))
            .where(ChatMessage.role == "assistant")
        )).scalar_one())
    return {"workspaces": len(ws_ids), "builds": builds, "chats": chats}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/me", response_model=MeResponse, summary="Current user's profile + counts")
async def get_me(user: User = Depends(require_user)):
    _require_neon()
    counts = await _rollup_counts(user.id)
    return MeResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at.isoformat(),
        auth_mode="jwt",
        workspace_count=counts["workspaces"],
        build_count=counts["builds"],
        chat_count=counts["chats"],
    )


@router.patch("/me", response_model=MeResponse, summary="Update the display name on the app User row")
async def update_me(body: UpdateMeRequest, user: User = Depends(require_user)):
    _require_neon()
    if body.display_name is None:
        # Nothing to change — return current profile as-is.
        return await get_me(user)

    async with get_session() as s:
        row = (await s.execute(select(User).where(User.id == user.id))).scalar_one()
        row.display_name = body.display_name
        await s.commit()

    invalidate_user_cache(str(user.id))
    record_audit(
        user.id, "user.profile.update",
        target_type="user", target_id=str(user.id),
        metadata={"fields": ["display_name"]},
    )
    # Refresh + reuse the full shape
    user.display_name = body.display_name
    return await get_me(user)


@router.get("/me/audit", response_model=AuditListResponse, summary="Paginated audit feed")
async def get_my_audit(
    limit: int = Query(default=50, ge=1, le=500),
    before_id: Optional[int] = Query(
        default=None,
        description="Cursor: return entries with id < this value. Omit for newest page.",
    ),
    workspace_id: Optional[str] = Query(
        default=None,
        description="Filter to a specific workspace the user owns.",
    ),
    action: Optional[str] = Query(
        default=None,
        description="Exact match on action string (e.g. 'workspace.create').",
    ),
    user: User = Depends(require_user),
):
    _require_neon()
    stmt = select(UserAuditLog).where(UserAuditLog.user_id == user.id)
    if before_id is not None:
        stmt = stmt.where(UserAuditLog.id < before_id)
    if workspace_id:
        import uuid as _uuid
        try:
            stmt = stmt.where(UserAuditLog.workspace_id == _uuid.UUID(workspace_id))
        except ValueError:
            raise HTTPException(status_code=400, detail="workspace_id must be a UUID")
    if action:
        stmt = stmt.where(UserAuditLog.action == action)
    # Fetch limit+1 to detect 'has_more' without a second round-trip.
    stmt = stmt.order_by(UserAuditLog.id.desc()).limit(limit + 1)

    async with get_session() as s:
        rows = (await s.execute(stmt)).scalars().all()

    has_more = len(rows) > limit
    rows = rows[:limit]
    entries = [
        AuditEntry(
            id=r.id,
            action=r.action,
            target_type=r.target_type,
            target_id=r.target_id,
            workspace_id=str(r.workspace_id) if r.workspace_id else None,
            metadata=r.audit_metadata,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
    return AuditListResponse(
        entries=entries,
        has_more=has_more,
        next_before_id=entries[-1].id if has_more and entries else None,
    )
