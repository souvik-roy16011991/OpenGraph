"""
Daily storage proration sweeper.

Once per day per workspace, read the latest successful build's usage
snapshot (``stats.usage.graph_payload_bytes`` + vector-count × dimension)
and debit the workspace's owner for 1/30 of the monthly storage rate.

Idempotency: the sweeper checks for an existing
``credit_transactions(reason='storage_daily', source_id=<workspace_id>)``
row with ``created_at >= today_utc`` before inserting. Re-running the
sweeper the same day is a no-op.

Zero-build workspaces are not billed (no usage snapshot = nothing to
meter). Documented user-facing behaviour: "storage charges begin after
your first successful build."

Called from the ``build_worker`` idle loop once every 30 minutes — cheap
enough to run opportunistically (one SELECT per workspace with active
builds), expensive enough that we don't want multiple workers racing on
it. Multiple workers IS safe (inserts either succeed or are dedup'd by
the existence check), but to avoid N workers each doing the work,
``worker_main`` gates on a shared last-sweep timestamp column.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, text

from src.billing.ledger import debit
from src.billing.rate_card import cost_storage_daily
from src.infra.db import get_session
from src.infra.db_models import BuildJobRow, Workspace

logger = logging.getLogger(__name__)


async def sweep_daily_storage() -> int:
    """Emit one storage-debit ledger row per workspace that has a completed
    build AND hasn't already been swept today. Returns count emitted.
    """
    today_utc_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    async with get_session() as s:
        # Find workspaces that have at least one successful build AND
        # haven't been swept today. One round-trip.
        rows = (await s.execute(text("""
            SELECT w.id AS workspace_id, w.user_id
            FROM workspaces w
            WHERE EXISTS (
              SELECT 1 FROM build_jobs bj
              WHERE bj.workspace_id = w.id AND bj.status = 'done'
            )
            AND NOT EXISTS (
              SELECT 1 FROM credit_transactions ct
              WHERE ct.source_type = 'workspace_storage'
                AND ct.source_id = w.id::text
                AND ct.reason = 'storage_daily'
                AND ct.created_at >= :today_start
            )
        """), {"today_start": today_utc_start})).fetchall()

    emitted = 0
    for row in rows:
        workspace_id = row.workspace_id
        user_id = row.user_id
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

            cost = cost_storage_daily(
                graph_payload_bytes=graph_bytes,
                vector_count=vec_count,
                vector_dimension=vec_dim,
            )
            if cost <= 0:
                continue

            await debit(
                user_id,
                cost,
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
                },
            )
            emitted += 1
        except Exception as exc:
            logger.warning(
                "storage sweep failed for workspace=%s: %s", workspace_id, exc,
            )

    if emitted:
        logger.info("storage sweep: emitted %d daily storage debits", emitted)
    return emitted
