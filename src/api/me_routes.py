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
from fastapi.responses import Response
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
    CreditTransaction,
    User,
    UserAuditLog,
    Workspace,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TrialRemaining(BaseModel):
    """Lifetime caps left on the free trial. All fields become ``None`` once
    the user is no longer on the trial tier (they represent caps, not usage)."""
    workspaces: Optional[int]
    builds: Optional[int]
    chats: Optional[int]


class BillingSummary(BaseModel):
    plan_tier: str                        # trial | payg | team
    subscription_credits: int
    topup_credits: int
    total_credits: int
    overdraft_limit: int
    trial_remaining: TrialRemaining


class MeResponse(BaseModel):
    id: str
    email: Optional[str]
    display_name: Optional[str]
    created_at: str
    auth_mode: str  # always 'jwt' — retained for API-client compatibility
    workspace_count: int
    build_count: int
    chat_count: int
    billing: BillingSummary


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


class CreditTransactionEntry(BaseModel):
    id: int
    delta_credits: int
    bucket: str            # subscription | topup
    reason: str            # build | chat | storage_daily | topup | subscription_grant | ...
    source_type: Optional[str]
    source_id: Optional[str]
    actor_type: str
    metadata: Optional[dict[str, Any]]
    created_at: str


class CreditTransactionListResponse(BaseModel):
    entries: list[CreditTransactionEntry]
    has_more: bool
    next_before_id: Optional[int]


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

async def _billing_summary(user_id, workspace_count: int) -> BillingSummary:
    """Load billing state for the profile card. Auto-seeds a trial account
    on first access so every /me call succeeds even for pre-billing users."""
    from src.billing.enforcement import (
        TRIAL_MAX_BUILDS, TRIAL_MAX_CHATS, TRIAL_MAX_WORKSPACES,
    )
    from src.billing.ledger import get_account
    acct = await get_account(user_id)
    is_trial = acct.plan_tier == "trial"
    trial_remaining = TrialRemaining(
        workspaces=(max(0, TRIAL_MAX_WORKSPACES - workspace_count) if is_trial else None),
        builds=(max(0, TRIAL_MAX_BUILDS - int(acct.trial_build_count or 0)) if is_trial else None),
        chats=(max(0, TRIAL_MAX_CHATS - int(acct.trial_chat_count or 0)) if is_trial else None),
    )
    sub = int(acct.subscription_credits or 0)
    top = int(acct.topup_credits or 0)
    return BillingSummary(
        plan_tier=acct.plan_tier,
        subscription_credits=sub,
        topup_credits=top,
        total_credits=sub + top,
        overdraft_limit=int(acct.overdraft_limit or -200),
        trial_remaining=trial_remaining,
    )


@router.get("/me", response_model=MeResponse, summary="Current user's profile + counts")
async def get_me(user: User = Depends(require_user)):
    _require_neon()
    counts = await _rollup_counts(user.id)
    billing = await _billing_summary(user.id, counts["workspaces"])
    return MeResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        created_at=user.created_at.isoformat(),
        auth_mode="jwt",
        workspace_count=counts["workspaces"],
        build_count=counts["builds"],
        chat_count=counts["chats"],
        billing=billing,
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


@router.get(
    "/me/billing/transactions",
    response_model=CreditTransactionListResponse,
    summary="Paginated credit-ledger history for the current user",
)
async def get_my_billing_transactions(
    limit: int = Query(default=50, ge=1, le=500),
    before_id: Optional[int] = Query(default=None),
    user: User = Depends(require_user),
):
    """Return the credit_transactions for the caller in descending id
    order. Cursor pagination via ``before_id``."""
    _require_neon()
    stmt = select(CreditTransaction).where(CreditTransaction.user_id == user.id)
    if before_id is not None:
        stmt = stmt.where(CreditTransaction.id < before_id)
    stmt = stmt.order_by(CreditTransaction.id.desc()).limit(limit + 1)
    async with get_session() as s:
        rows = (await s.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    entries = [
        CreditTransactionEntry(
            id=r.id,
            delta_credits=int(r.delta_credits),
            bucket=r.bucket,
            reason=r.reason,
            source_type=r.source_type,
            source_id=r.source_id,
            actor_type=r.actor_type,
            metadata=r.metadata_json,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]
    return CreditTransactionListResponse(
        entries=entries,
        has_more=has_more,
        next_before_id=entries[-1].id if has_more and entries else None,
    )


@router.get(
    "/me/billing/transactions.csv",
    summary="Downloadable CSV of the credit-ledger history",
)
async def export_my_billing_transactions_csv(user: User = Depends(require_user)):
    """Export every credit_transactions row for the caller as RFC-4180 CSV.

    Served with a ``Content-Disposition: attachment`` header so browsers
    save the file rather than rendering it. No pagination — users will
    typically have hundreds of rows, not millions; if that changes we can
    switch to ``StreamingResponse`` with chunked generation.
    """
    import csv
    import io

    _require_neon()
    async with get_session() as s:
        rows = (await s.execute(
            select(CreditTransaction)
            .where(CreditTransaction.user_id == user.id)
            .order_by(CreditTransaction.id.desc())
        )).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "id", "created_at", "delta_credits", "bucket", "reason",
        "source_type", "source_id", "actor_type", "metadata_json",
    ])
    for r in rows:
        writer.writerow([
            r.id,
            r.created_at.isoformat() if r.created_at else "",
            int(r.delta_credits),
            r.bucket,
            r.reason,
            r.source_type or "",
            r.source_id or "",
            r.actor_type,
            # ``metadata_json`` is a JSONB dict — render as a compact JSON
            # string so Excel / accounting software can at least ingest it.
            __import__("json").dumps(r.metadata_json) if r.metadata_json else "",
        ])

    # Filename carries the user id so exports from different accounts don't
    # collide in a downloads folder.
    filename = f"billing-transactions-{str(user.id)[:8]}.csv"
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


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
