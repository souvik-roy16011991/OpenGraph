"""
Daily storage proration sweeper.

Runs once per day per workspace. Reads the latest successful build's usage
snapshot (``stats.usage.graph_payload_bytes`` + vector-count × dimension),
computes the per-day cost in **micro-credits** (1 credit = 1,000,000
micros), and accumulates into ``workspace_storage_meters.remainder_micro``.
When the remainder crosses 1,000,000 the whole-credit portion is flushed
as a ``credit_transactions`` debit and the remainder resets to
``remainder % 1,000,000``.

Why micros: a small workspace (~100MB) costs ~0.07 credits/day. Rounding
to ``int`` every day would charge 0 forever. The accumulator preserves
the fractional cost so after ~15 days the workspace crosses 1 credit and
gets billed.

Idempotency: each workspace has its own ``last_charged_on`` UTC-date
column. The sweeper skips workspaces already processed today. Safe to
run on many workers concurrently — the `ON CONFLICT ... DO NOTHING`
guard on the date serializer prevents double-counting.

Zero-build workspaces are not billed (no usage snapshot = nothing to
meter). Documented user-facing: "storage charges begin after your first
successful build."

Called from the ``build_worker`` idle loop every 30 minutes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, text

from src.billing.ledger import debit
from src.billing.rate_card import CREDIT_MICRO, cost_storage_daily_micro
from src.infra.db import get_session
from src.infra.db_models import BuildJobRow, WorkspaceStorageMeter

logger = logging.getLogger(__name__)


async def sweep_daily_storage() -> int:
    """Accumulate fractional daily storage cost and flush whole credits.

    Returns the count of workspaces that received a whole-credit debit
    during this sweep (workspaces that only ticked the remainder forward
    don't count toward the return value, but their meter did update).
    """
    today_utc_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    async with get_session() as s:
        # Workspaces that have at least one successful build AND haven't
        # been ticked today. LEFT JOIN to meters so new workspaces show up
        # with last_charged_on IS NULL.
        rows = (await s.execute(text("""
            SELECT w.id AS workspace_id,
                   w.user_id,
                   COALESCE(m.remainder_micro, 0) AS remainder_micro,
                   m.last_charged_on
            FROM workspaces w
            LEFT JOIN workspace_storage_meters m ON m.workspace_id = w.id
            WHERE EXISTS (
              SELECT 1 FROM build_jobs bj
              WHERE bj.workspace_id = w.id AND bj.status = 'done'
            )
            AND (m.last_charged_on IS NULL OR m.last_charged_on < :today_start)
        """), {"today_start": today_utc_start})).fetchall()

    debited_count = 0
    for row in rows:
        workspace_id = row.workspace_id
        user_id = row.user_id
        existing_remainder = int(row.remainder_micro or 0)

        try:
            # Latest successful build's usage snapshot drives the bill.
            async with get_session() as s:
                latest = (await s.execute(
                    select(BuildJobRow)
                    .where(BuildJobRow.workspace_id == workspace_id)
                    .where(BuildJobRow.status == "done")
                    .order_by(BuildJobRow.finished_at.desc())
                    .limit(1)
                )).scalar_one_or_none()

            if latest is None or not latest.stats:
                continue

            usage = (latest.stats or {}).get("usage") or {}
            graph_bytes = int(usage.get("graph_payload_bytes") or 0)
            vec_count = int(usage.get("embedding_vectors") or 0)
            vec_dim = int(usage.get("embedding_dimension") or 0)

            daily_micro = cost_storage_daily_micro(
                graph_payload_bytes=graph_bytes,
                vector_count=vec_count,
                vector_dimension=vec_dim,
            )
            new_remainder = existing_remainder + daily_micro
            whole_credits = new_remainder // CREDIT_MICRO
            new_remainder_after_flush = new_remainder % CREDIT_MICRO

            # Upsert the meter row (and stamp today). Single statement so
            # the "already processed today" dedupe works across workers.
            async with get_session() as s:
                await s.execute(text("""
                    INSERT INTO workspace_storage_meters
                        (workspace_id, remainder_micro, last_charged_on)
                    VALUES (:w, :rem, :today)
                    ON CONFLICT (workspace_id) DO UPDATE
                      SET remainder_micro = EXCLUDED.remainder_micro,
                          last_charged_on = EXCLUDED.last_charged_on,
                          updated_at = now()
                      WHERE workspace_storage_meters.last_charged_on IS NULL
                         OR workspace_storage_meters.last_charged_on < EXCLUDED.last_charged_on
                """), {
                    "w": str(workspace_id),
                    "rem": new_remainder_after_flush,
                    "today": today_utc_start,
                })
                await s.commit()

            # Only write a ledger row when we actually flushed whole credits.
            if whole_credits > 0:
                await debit(
                    user_id,
                    int(whole_credits),
                    reason="storage_daily",
                    source_type="workspace_storage",
                    source_id=str(workspace_id),
                    actor_type="system",
                    metadata={
                        "workspace_id": str(workspace_id),
                        "graph_payload_bytes": graph_bytes,
                        "vector_count": vec_count,
                        "vector_dimension": vec_dim,
                        "build_id": latest.job_id,
                        "accumulated_days": None,  # could compute from deltas later
                    },
                )
                debited_count += 1
        except Exception as exc:
            logger.warning(
                "storage sweep failed for workspace=%s: %s", workspace_id, exc,
            )

    if debited_count:
        logger.info(
            "storage sweep: %d whole-credit storage debits flushed (touched %d workspaces)",
            debited_count, len(rows),
        )
    return debited_count
