"""
Auth-adjacent backend routes.

Signup, login, token refresh, and OAuth are handled entirely by Supabase Auth
on the frontend — the backend never sees passwords or mints tokens.

The one endpoint kept here is logout, which is a client-side no-op (Supabase
manages token revocation) but is called fire-and-forget so the audit trail
records the event.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.auth import require_user
from src.infra.audit import record_audit
from src.infra.db_models import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/logout", summary="Record logout event in audit trail")
async def logout(user: User = Depends(require_user)) -> dict:
    try:
        record_audit(user.id, "auth.logout", target_type="user", target_id=str(user.id))
    except Exception:
        pass
    return {"ok": True}
