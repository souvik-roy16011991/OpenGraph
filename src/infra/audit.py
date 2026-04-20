"""
Append-only user audit trail recorder.

Public API: :func:`record_audit` — call from any route after a mutation,
fire-and-forget. Failures are logged and swallowed so audit persistence
never blocks or breaks the primary request.

Vocab for `action` strings (dotted `<object>.<verb>`):

  workspace.create, workspace.update, workspace.delete
  template.instantiate
  config.domain.update, config.graph.update
  build.start
  file.upload, file.delete
  chat.query
  llm.preference.change
  auth.signup  — emitted once per user, the first time we see their JWT

Consumers (UI feed, downstream analytics) are expected to be tolerant of
new `action` values appearing without advance notice.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional, Union

from src.config import USE_NEON
from src.infra.db import fire_and_forget, get_session
from src.infra.db_models import UserAuditLog

logger = logging.getLogger(__name__)


def _coerce_uuid(v: Union[str, uuid.UUID, None]) -> Optional[uuid.UUID]:
    if v is None:
        return None
    if isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError):
        return None


async def _write_audit(
    user_id: uuid.UUID,
    action: str,
    target_type: Optional[str],
    target_id: Optional[str],
    workspace_id: Optional[uuid.UUID],
    metadata: Optional[dict[str, Any]],
) -> None:
    try:
        async with get_session() as s:
            s.add(UserAuditLog(
                user_id=user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                workspace_id=workspace_id,
                audit_metadata=metadata or None,
            ))
            await s.commit()
    except Exception as exc:
        logger.warning("audit insert failed (%s): %s", action, exc)


def record_audit(
    user_id: Union[str, uuid.UUID, None],
    action: str,
    *,
    target_type: Optional[str] = None,
    target_id: Union[str, uuid.UUID, int, None] = None,
    workspace_id: Union[str, uuid.UUID, None] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Record one audit event, fire-and-forget.

    - Silently no-ops when Neon isn't configured.
    - Silently no-ops when we don't have a user (never audit anonymous
      actions — the whole table is user-scoped by design).
    - Scheduling uses the main event loop via ``fire_and_forget``, so this
      is safe to call from sync code, async handlers, and background
      threads alike.
    """
    if not USE_NEON:
        return
    uid = _coerce_uuid(user_id)
    if uid is None:
        return
    wid = _coerce_uuid(workspace_id)
    tid = None if target_id is None else str(target_id)
    fire_and_forget(_write_audit(uid, action, target_type, tid, wid, metadata))
