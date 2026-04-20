"""
Anonymous-workspace migration CLI.

Before Phase 1d (auth enforcement) lands, every row in ``workspaces`` belongs
to the synthetic ``__anonymous__`` user. This script cleans that up so a real
multi-tenant deploy has no ownerless data drifting around. **Irreversible** —
take a Neon snapshot first.

Three modes:
  --dry-run       (default) report how many workspaces + children would be touched
  --delete-all    delete every workspace owned by __anonymous__ (also clears
                  Memgraph / Qdrant / Vercel Blob via the existing
                  delete_workspace flow), then delete the anonymous user row
  --assign-to <stack_user_id>
                  reassign every anon-owned workspace to the User with that
                  stack_user_id (creates the user row on the fly if missing)

Usage::
    python scripts/migrate_anon_workspaces.py --dry-run
    python scripts/migrate_anon_workspaces.py --delete-all
    python scripts/migrate_anon_workspaces.py --assign-to 'stack-sub-1234'
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate_anon")


async def _summary() -> dict:
    """Return counts of rows owned by the anonymous user."""
    from sqlalchemy import func, select
    from src.infra.db import get_session
    from src.infra.db_models import (
        ANONYMOUS_STACK_ID,
        BuildJobRow,
        ChatSession,
        ConfigVersion,
        KbUpload,
        User,
        Workspace,
        WorkspaceFile,
    )

    async with get_session() as s:
        anon = (await s.execute(
            select(User).where(User.stack_user_id == ANONYMOUS_STACK_ID)
        )).scalar_one_or_none()
        if anon is None:
            return {"anon_user": False, "workspaces": 0}

        ws_rows = (await s.execute(
            select(Workspace.id).where(Workspace.user_id == anon.id)
        )).all()
        ws_ids = [r[0] for r in ws_rows]
        if not ws_ids:
            return {"anon_user": True, "workspaces": 0, "anon_user_id": str(anon.id)}

        async def _count(model, col):
            r = await s.execute(
                select(func.count()).select_from(model).where(col.in_(ws_ids))
            )
            return int(r.scalar() or 0)

        return {
            "anon_user": True,
            "anon_user_id": str(anon.id),
            "workspaces": len(ws_ids),
            "workspace_files": await _count(WorkspaceFile, WorkspaceFile.workspace_id),
            "build_jobs": await _count(BuildJobRow, BuildJobRow.workspace_id),
            "chat_sessions": await _count(ChatSession, ChatSession.workspace_id),
            "config_versions": await _count(ConfigVersion, ConfigVersion.workspace_id),
            "kb_uploads": await _count(KbUpload, KbUpload.workspace_id),
            "workspace_ids": [str(w) for w in ws_ids],
        }


async def _delete_all() -> None:
    """Iterate each anon workspace and run the full cascade (Memgraph clear,
    Qdrant drop, Blob wipe, Neon cascade) via the auth-free helper."""
    import uuid
    from sqlalchemy import select, delete
    from src.api.workspace_routes import delete_workspace_cascade
    from src.infra.db import get_session
    from src.infra.db_models import ANONYMOUS_STACK_ID, User, Workspace

    async with get_session() as s:
        anon = (await s.execute(
            select(User).where(User.stack_user_id == ANONYMOUS_STACK_ID)
        )).scalar_one_or_none()
        if anon is None:
            logger.info("No anonymous user present; nothing to delete.")
            return
        ws_ids = [r[0] for r in (await s.execute(
            select(Workspace.id).where(Workspace.user_id == anon.id)
        )).all()]

    for wid in ws_ids:
        try:
            logger.info("deleting workspace %s ...", wid)
            await delete_workspace_cascade(uuid.UUID(str(wid)))
        except Exception as exc:
            logger.error("delete_workspace(%s) failed: %s", wid, exc)

    # Finally drop the anon user row itself.
    async with get_session() as s:
        await s.execute(delete(User).where(User.stack_user_id == ANONYMOUS_STACK_ID))
        await s.commit()
    logger.info("removed anonymous user row.")


async def _assign_to(stack_user_id: str) -> None:
    from sqlalchemy import select, update
    from src.infra.db import get_session
    from src.infra.db_models import ANONYMOUS_STACK_ID, User, Workspace

    async with get_session() as s:
        anon = (await s.execute(
            select(User).where(User.stack_user_id == ANONYMOUS_STACK_ID)
        )).scalar_one_or_none()
        if anon is None:
            logger.info("No anonymous user present; nothing to reassign.")
            return
        target = (await s.execute(
            select(User).where(User.stack_user_id == stack_user_id)
        )).scalar_one_or_none()
        if target is None:
            target = User(stack_user_id=stack_user_id, display_name=f"Migrated owner ({stack_user_id})")
            s.add(target)
            await s.commit()
            await s.refresh(target)
            logger.info("created destination user %s", target.id)

        await s.execute(
            update(Workspace).where(Workspace.user_id == anon.id).values(user_id=target.id)
        )
        await s.commit()
        logger.info("reassigned every anon-owned workspace to user %s.", target.id)


async def _main(args) -> None:
    summary = await _summary()
    logger.info("current state: %s", summary)

    if args.delete_all:
        logger.warning("about to DELETE all anon-owned workspaces")
        await _delete_all()
        return
    if args.assign_to:
        logger.info("reassigning anon workspaces to stack_user_id=%s", args.assign_to)
        await _assign_to(args.assign_to)
        return
    logger.info("dry-run complete. use --delete-all or --assign-to to apply.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: just report counts")
    mode.add_argument("--delete-all", action="store_true", help="wipe every anon-owned workspace")
    mode.add_argument("--assign-to", metavar="STACK_USER_ID", help="reassign anon workspaces to this user")
    asyncio.run(_main(p.parse_args()))
