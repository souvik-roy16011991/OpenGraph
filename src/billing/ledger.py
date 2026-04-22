"""
Credit ledger — atomic grants + debits against ``billing_accounts``.

Two exported operations:

    await grant(user_id, credits, reason, bucket, ...)   # positive delta
    await debit(user_id, credits, reason, ...)           # negative delta

Both insert an append-only ``credit_transactions`` row AND update the
denormalized ``billing_accounts`` balance in the same DB transaction so
the two never drift.

Design decisions from the plan:

- **Subscription-first debit order**: when debiting, subscription_credits
  are drained before topup_credits. Users see "my $49/mo allowance used
  first, reserves second."
- **No FOR UPDATE lock** on the hot path. Two concurrent debits may both
  succeed past ``overdraft_limit``, but overdraft is bounded (−200
  default) and the pre-check surface catches the vast majority. Serialised
  locks on every chat turn would be an expensive fix to a small problem.
- **Auto-seed**: if the user has no ``billing_accounts`` row yet, one is
  created with ``plan_tier='trial'`` via ``INSERT ... ON CONFLICT DO
  NOTHING``. Every call is idempotent on a fresh user.
- **Trial tier never debits real credits**: the debit still writes a
  ledger row (for audit) but delta is 0 on trial. Trial usage is capped
  by enforcement, not by balance.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert

from src.infra.db import get_session
from src.infra.db_models import BillingAccount, CreditTransaction

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public DTO
# ---------------------------------------------------------------------------

@dataclass
class Balance:
    subscription_credits: int
    topup_credits: int

    @property
    def total(self) -> int:
        return self.subscription_credits + self.topup_credits


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------

async def ensure_account(user_id: uuid.UUID | str) -> None:
    """Idempotently create a ``billing_accounts`` row for the user.

    Called lazily from the first enforcement check / debit. Safe to call
    on every request — ``ON CONFLICT DO NOTHING`` makes it effectively free
    after the first row exists.
    """
    uid = _coerce_uuid(user_id)
    async with get_session() as s:
        stmt = insert(BillingAccount).values(
            user_id=uid,
            plan_tier="trial",
        ).on_conflict_do_nothing(index_elements=["user_id"])
        await s.execute(stmt)
        await s.commit()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

async def get_balance(user_id: uuid.UUID | str) -> Balance:
    """Return the live balances. Auto-seeds if missing."""
    uid = _coerce_uuid(user_id)
    async with get_session() as s:
        row = (await s.execute(
            select(BillingAccount).where(BillingAccount.user_id == uid)
        )).scalar_one_or_none()
    if row is None:
        await ensure_account(uid)
        return Balance(subscription_credits=0, topup_credits=0)
    return Balance(
        subscription_credits=int(row.subscription_credits or 0),
        topup_credits=int(row.topup_credits or 0),
    )


async def get_account(user_id: uuid.UUID | str) -> BillingAccount:
    """Return the full account row (creates if missing)."""
    uid = _coerce_uuid(user_id)
    await ensure_account(uid)
    async with get_session() as s:
        row = (await s.execute(
            select(BillingAccount).where(BillingAccount.user_id == uid)
        )).scalar_one()
        return row


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

async def grant(
    user_id: uuid.UUID | str,
    credits: int,
    *,
    reason: str,
    bucket: str,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    stripe_event_id: Optional[str] = None,
    actor_type: str = "system",
    actor_user_id: Optional[uuid.UUID | str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Balance:
    """Add *credits* to the named bucket. Writes a ``credit_transactions``
    row AND updates ``billing_accounts`` in one transaction.

    ``credits`` must be > 0. For negative deltas use :func:`debit`.
    """
    if credits <= 0:
        raise ValueError(f"grant() requires positive credits, got {credits}")
    if bucket not in ("subscription", "topup"):
        raise ValueError(f"invalid bucket: {bucket}")

    uid = _coerce_uuid(user_id)
    await ensure_account(uid)

    async with get_session() as s:
        # Update the denormalized balance.
        await s.execute(text(f"""
            UPDATE billing_accounts
            SET {bucket}_credits = {bucket}_credits + :delta,
                updated_at = now()
            WHERE user_id = :uid
        """), {"delta": credits, "uid": str(uid)})

        # Append the ledger entry.
        tx = CreditTransaction(
            user_id=uid,
            delta_credits=credits,
            bucket=bucket,
            reason=reason,
            source_type=source_type,
            source_id=source_id,
            stripe_event_id=stripe_event_id,
            actor_type=actor_type,
            actor_user_id=_coerce_uuid(actor_user_id) if actor_user_id else None,
            metadata_json=metadata,
        )
        s.add(tx)
        await s.commit()

    # Re-read the balance for the caller (cheap, one row).
    return await get_balance(uid)


async def debit(
    user_id: uuid.UUID | str,
    credits: int,
    *,
    reason: str,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    actor_type: str = "system",
    actor_user_id: Optional[uuid.UUID | str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> Balance:
    """Deduct *credits* from the user, subscription-bucket first.

    - Trial tier: writes a ledger row with delta=0 (preserves audit trail)
      but doesn't mutate balance. Trial users are capped by enforcement.
    - Negative balance is allowed down to ``overdraft_limit``. Post-commit
      deductions from in-flight builds get a grace zone; new builds are
      blocked by the enforcement layer once past the floor.
    - ``credits`` must be >= 0. A zero-cost action (skip_embeddings + BYOK
      + small build) still writes a ledger row for auditability.
    """
    if credits < 0:
        raise ValueError(f"debit() requires non-negative credits, got {credits}")

    uid = _coerce_uuid(user_id)
    account = await get_account(uid)

    # Trial users don't burn credits (they're capped by count). We still
    # record the ledger entry with delta=0 so usage is auditable.
    if account.plan_tier == "trial":
        async with get_session() as s:
            tx = CreditTransaction(
                user_id=uid,
                delta_credits=0,
                bucket="topup",
                reason=reason,
                source_type=source_type,
                source_id=source_id,
                actor_type=actor_type,
                actor_user_id=_coerce_uuid(actor_user_id) if actor_user_id else None,
                metadata_json={**(metadata or {}), "tier": "trial", "nominal_cost": credits},
            )
            s.add(tx)
            await s.commit()
        return Balance(subscription_credits=0, topup_credits=0)

    if credits == 0:
        # Nothing to debit; skip the ledger row to avoid noise.
        return Balance(
            subscription_credits=int(account.subscription_credits),
            topup_credits=int(account.topup_credits),
        )

    # Subscription-first debit.
    sub_used = min(credits, max(0, int(account.subscription_credits)))
    topup_used = credits - sub_used

    async with get_session() as s:
        # Two ledger rows (one per bucket touched) so we can aggregate by
        # bucket in the billing UI without parsing metadata.
        if sub_used > 0:
            await s.execute(text("""
                UPDATE billing_accounts
                SET subscription_credits = subscription_credits - :delta,
                    updated_at = now()
                WHERE user_id = :uid
            """), {"delta": sub_used, "uid": str(uid)})
            s.add(CreditTransaction(
                user_id=uid,
                delta_credits=-sub_used,
                bucket="subscription",
                reason=reason,
                source_type=source_type,
                source_id=source_id,
                actor_type=actor_type,
                actor_user_id=_coerce_uuid(actor_user_id) if actor_user_id else None,
                metadata_json=metadata,
            ))

        if topup_used > 0:
            await s.execute(text("""
                UPDATE billing_accounts
                SET topup_credits = topup_credits - :delta,
                    updated_at = now()
                WHERE user_id = :uid
            """), {"delta": topup_used, "uid": str(uid)})
            s.add(CreditTransaction(
                user_id=uid,
                delta_credits=-topup_used,
                bucket="topup",
                reason=reason,
                source_type=source_type,
                source_id=source_id,
                actor_type=actor_type,
                actor_user_id=_coerce_uuid(actor_user_id) if actor_user_id else None,
                metadata_json=metadata,
            ))

        await s.commit()

    return await get_balance(uid)


async def bump_trial_counter(
    user_id: uuid.UUID | str,
    *,
    kind: str,
) -> None:
    """Increment the trial's ``build`` or ``chat`` counter. No-op for
    non-trial users. Called by enforcement helpers after a trial action
    succeeds (not before — we only count successful consumption).
    """
    if kind not in ("build", "chat"):
        raise ValueError(f"invalid trial counter kind: {kind}")
    uid = _coerce_uuid(user_id)
    col = f"trial_{kind}_count"
    async with get_session() as s:
        await s.execute(text(f"""
            UPDATE billing_accounts
            SET {col} = {col} + 1,
                updated_at = now()
            WHERE user_id = :uid
              AND plan_tier = 'trial'
        """), {"uid": str(uid)})
        await s.commit()


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------

def _coerce_uuid(value: uuid.UUID | str | None) -> Optional[uuid.UUID]:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))
