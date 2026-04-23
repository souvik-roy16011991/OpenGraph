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
# Document-parse enforcement (vision-OCR ingestion)
# ---------------------------------------------------------------------------
# Three independent checks, any of which rejects the enqueue:
#
#   1. Standard balance / trial-cap check (same as build/chat).
#   2. Per-user concurrent queue cap — prevents a 10k-doc drop from
#      monopolising worker capacity. Tunable via USER_PARSE_QUEUE_CAP
#      (default 50).
#   3. Daily cost ceiling — rolling 24h sum of ``document_parse`` debits
#      against USER_DAILY_PARSE_USD (default $20). Stops a runaway
#      burst from turning into a surprise bill.
#
# Each check raises a distinct ``reason`` on PaymentRequired so the
# frontend can show a targeted error.

import os as _os
from datetime import datetime as _datetime, timedelta as _timedelta, timezone as _timezone

USER_PARSE_QUEUE_CAP = max(1, int(_os.environ.get("USER_PARSE_QUEUE_CAP", "50")))
USER_DAILY_PARSE_USD = float(_os.environ.get("USER_DAILY_PARSE_USD", "20.00"))


async def check_document_parse_allowed(user_id: uuid.UUID | str) -> None:
    """Gate a vision-OCR parse enqueue.

    Called once per file during the upload endpoint. Cheap: two
    narrow COUNT/SUM queries plus the existing balance read. The
    ``_check_trial_or_balance`` call reuses the same trial-bucket
    counter as builds — parses count as builds for trial users so
    they can't infinitely burn through the free tier via OCR.
    """
    from src.infra.db_models import CreditTransaction, ParseJobRow
    from sqlalchemy import func, and_

    uid = _coerce_uuid(user_id)
    await ensure_account(uid)

    async with get_session() as s:
        acct = (await s.execute(
            select(BillingAccount).where(BillingAccount.user_id == uid)
        )).scalar_one()

        # 1. Trial cap / overdraft floor.
        _check_trial_or_balance(
            acct,
            trial_kind="document_parse",
            trial_current=int(acct.trial_build_count or 0),
            trial_cap=TRIAL_MAX_BUILDS,
        )

        # 2. Concurrent-queue cap.
        queue_depth = (await s.execute(
            select(func.count(ParseJobRow.job_id))
            .where(and_(
                ParseJobRow.user_id == uid,
                ParseJobRow.status.in_(("queued", "running")),
            ))
        )).scalar_one()
        if queue_depth >= USER_PARSE_QUEUE_CAP:
            raise PaymentRequired(
                reason="parse_queue_cap_exceeded",
                queue_depth=int(queue_depth),
                cap=USER_PARSE_QUEUE_CAP,
            )

        # 3. Rolling 24h cost ceiling. The ledger stores credits; we
        # convert to approximate USD at 0.01 USD/credit (the rate used
        # everywhere else; see rate_card). The check tolerates the
        # (typical) case where no transactions exist yet — SUM returns
        # NULL which SQLAlchemy hands back as None.
        since = _datetime.now(_timezone.utc) - _timedelta(hours=24)
        spent_credits = (await s.execute(
            select(func.coalesce(func.sum(func.abs(CreditTransaction.delta_credits)), 0))
            .where(and_(
                CreditTransaction.user_id == uid,
                CreditTransaction.reason == "document_parse",
                CreditTransaction.created_at >= since,
            ))
        )).scalar_one()
        # credits ≈ USD * 100 in this system; see src.billing.rate_card
        # for the per-1K-token rates that feed into it.
        spent_usd = float(spent_credits or 0) / 100.0
        if spent_usd >= USER_DAILY_PARSE_USD:
            raise PaymentRequired(
                reason="daily_parse_cost_exceeded",
                spent_usd=round(spent_usd, 2),
                cap_usd=USER_DAILY_PARSE_USD,
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
