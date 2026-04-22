"""
Pre-action billing checks — called on the hot path of build/chat/workspace
endpoints before doing work. Each helper is async, reads the billing
account once, and either returns cleanly or raises ``PaymentRequired``
(HTTP 402) with a structured detail the frontend can render.

Two classes of check:

- **Trial caps** (plan_tier='trial'): enforce lifetime 1 workspace / 1
  build / 5 chats. Use ``trial_*_count`` on ``billing_accounts``.
- **Overdraft floor** (plan_tier in payg/team): block new actions once
  ``subscription_credits + topup_credits < overdraft_limit`` (default
  −200). Post-commit deductions from in-flight work can still push the
  balance further negative; the floor is "allowed to be this negative
  already, no more new work."
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select

from src.billing.ledger import ensure_account
from src.infra.db import get_session
from src.infra.db_models import BillingAccount, Workspace

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Trial caps — lifetime, not monthly
# ---------------------------------------------------------------------------

TRIAL_MAX_WORKSPACES = 1
TRIAL_MAX_BUILDS = 1
TRIAL_MAX_CHATS = 5

# Per-tier workspace caps (once past trial).
WORKSPACE_CAP_BY_TIER: dict[str, int] = {
    "trial": TRIAL_MAX_WORKSPACES,
    "payg": 25,
    "team": 50,
}


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class PaymentRequired(HTTPException):
    """HTTP 402 with a structured detail. Surfaces plan/balance info the
    frontend uses to render an upgrade CTA without a second round-trip."""

    def __init__(self, reason: str, **extra):
        super().__init__(
            status_code=402,
            detail={"reason": reason, **extra},
        )


# ---------------------------------------------------------------------------
# Public checks
# ---------------------------------------------------------------------------

async def check_workspace_create_allowed(user_id: uuid.UUID | str) -> None:
    """Reject creating a new workspace past the per-tier cap.

    Trial: 1 workspace lifetime. PAYG: 25. Team: 50. The limit is on
    *user-owned* workspaces only; workspaces the user can *see* via a
    future Team membership don't count against their cap.
    """
    uid = _coerce_uuid(user_id)
    await ensure_account(uid)

    async with get_session() as s:
        acct = (await s.execute(
            select(BillingAccount).where(BillingAccount.user_id == uid)
        )).scalar_one()

        cap = WORKSPACE_CAP_BY_TIER.get(acct.plan_tier, TRIAL_MAX_WORKSPACES)

        current = (await s.execute(
            select(func.count(Workspace.id)).where(Workspace.user_id == uid)
        )).scalar_one()

    if int(current or 0) >= cap:
        logger.info(
            "workspace cap hit: user=%s tier=%s current=%d cap=%d",
            uid, acct.plan_tier, current, cap,
        )
        raise PaymentRequired(
            reason="workspace_cap_exceeded",
            plan_tier=acct.plan_tier,
            current=int(current),
            cap=cap,
        )


async def check_build_allowed(user_id: uuid.UUID | str) -> None:
    """Reject a build POST on trial lifetime cap hit or PAYG/Team overdraft."""
    uid = _coerce_uuid(user_id)
    await ensure_account(uid)

    async with get_session() as s:
        acct = (await s.execute(
            select(BillingAccount).where(BillingAccount.user_id == uid)
        )).scalar_one()

    _check_trial_or_balance(
        acct,
        trial_kind="build",
        trial_current=int(acct.trial_build_count or 0),
        trial_cap=TRIAL_MAX_BUILDS,
    )


async def check_chat_allowed(user_id: uuid.UUID | str) -> None:
    """Reject a chat POST on trial lifetime cap hit or PAYG/Team overdraft."""
    uid = _coerce_uuid(user_id)
    await ensure_account(uid)

    async with get_session() as s:
        acct = (await s.execute(
            select(BillingAccount).where(BillingAccount.user_id == uid)
        )).scalar_one()

    _check_trial_or_balance(
        acct,
        trial_kind="chat",
        trial_current=int(acct.trial_chat_count or 0),
        trial_cap=TRIAL_MAX_CHATS,
    )


# ---------------------------------------------------------------------------
# Shared logic
# ---------------------------------------------------------------------------

def _check_trial_or_balance(
    acct: BillingAccount,
    *,
    trial_kind: str,
    trial_current: int,
    trial_cap: int,
) -> None:
    if acct.plan_tier == "trial":
        if trial_current >= trial_cap:
            raise PaymentRequired(
                reason="trial_cap_exceeded",
                trial_kind=trial_kind,
                current=trial_current,
                cap=trial_cap,
            )
        return

    # PAYG / Team: check balance against overdraft floor.
    balance = int(acct.subscription_credits or 0) + int(acct.topup_credits or 0)
    floor = int(acct.overdraft_limit or -200)
    if balance < floor:
        raise PaymentRequired(
            reason="insufficient_credits",
            plan_tier=acct.plan_tier,
            subscription_credits=int(acct.subscription_credits or 0),
            topup_credits=int(acct.topup_credits or 0),
            overdraft_limit=floor,
        )


def _coerce_uuid(value: uuid.UUID | str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))
