"""
Developer API-key management for the signed-in user (JWT auth).

These routes power the ``/api-keys`` dashboard page. They are intentionally
NOT reachable via API-key auth — only a logged-in browser session can mint
or revoke keys, otherwise a stolen key could mint siblings and extend its
own lifetime.

Key wire format: ``og_live_<32 url-safe base64 chars>``. The plaintext is
shown exactly once in the ``POST /keys`` response; afterwards the server
stores only a bcrypt hash and an indexed prefix. Rotations happen by
revoking + creating a new key (no in-place rotation in v1).
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone
from typing import Optional

import bcrypt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update

from src.api.api_key_auth import _KEY_MARKER, _PREFIX_LEN, invalidate_api_key_cache
from src.api.auth import require_user
from src.config import USE_NEON
from src.infra.audit import record_audit
from src.infra.db import get_session
from src.infra.db_models import ApiKey, User, Workspace

logger = logging.getLogger(__name__)

router = APIRouter(tags=["api-keys"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    workspace_id: Optional[str] = Field(
        default=None,
        description="Pin this key to a single workspace. Omit for account-scoped keys.",
    )
    rate_limit_rpm: Optional[int] = Field(
        default=None, ge=1, le=10_000,
        description="Per-minute request cap. Defaults to 60 when omitted.",
    )


class ApiKeyPublic(BaseModel):
    """What the dashboard sees — never includes the plaintext secret."""
    id: str
    name: str
    prefix: str
    workspace_id: Optional[str] = None
    rate_limit_rpm: int
    scopes: list[str]
    created_at: datetime
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class ApiKeyCreateResponse(BaseModel):
    """POST /keys response — includes the plaintext ONCE. Never stored.

    The dashboard is responsible for surfacing the ``plaintext`` exactly
    once with a copy button; on dialog close the value is lost forever.
    """
    key: ApiKeyPublic
    plaintext: str = Field(
        description=(
            "The full secret key. Copy it now — the server stores only a "
            "hash. If you lose it, revoke and create a new one."
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_plaintext() -> str:
    """Mint ``og_live_<32 char urlsafe>``.

    ``secrets.token_urlsafe(24)`` gives 32 base64 chars of cryptographic
    randomness — 192 bits of entropy, well above any reasonable guessing
    threshold. The prefix is a fixed marker so middleware can disambiguate
    API keys from JWTs that share the same Authorization header.
    """
    return _KEY_MARKER + secrets.token_urlsafe(24)


def _to_public(row: ApiKey) -> ApiKeyPublic:
    return ApiKeyPublic(
        id=str(row.id),
        name=row.name,
        prefix=row.prefix,
        workspace_id=str(row.workspace_id) if row.workspace_id else None,
        rate_limit_rpm=int(row.rate_limit_rpm or 60),
        scopes=list(row.scopes or []),
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


def _require_neon() -> None:
    if not USE_NEON:
        raise HTTPException(
            status_code=503,
            detail="API keys require DATABASE_URL (Neon).",
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post(
    "/keys",
    response_model=ApiKeyCreateResponse,
    status_code=201,
    summary="Mint a new developer API key (plaintext returned once)",
)
async def create_key(
    body: ApiKeyCreate,
    user: User = Depends(require_user),
) -> ApiKeyCreateResponse:
    _require_neon()

    workspace_uuid: Optional[uuid.UUID] = None
    if body.workspace_id:
        try:
            workspace_uuid = uuid.UUID(body.workspace_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="workspace_id must be a UUID.")
        async with get_session() as s:
            owner = (await s.execute(
                select(Workspace.user_id).where(
                    Workspace.id == workspace_uuid,
                    Workspace.deleted_at.is_(None),
                )
            )).scalar_one_or_none()
        if owner is None or owner != user.id:
            raise HTTPException(status_code=404, detail="Workspace not found.")

    plaintext = _generate_plaintext()
    prefix = plaintext[:_PREFIX_LEN]
    key_hash = bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    row = ApiKey(
        user_id=user.id,
        workspace_id=workspace_uuid,
        name=body.name,
        prefix=prefix,
        key_hash=key_hash,
        scopes=["ext:read"],
        rate_limit_rpm=body.rate_limit_rpm or 60,
    )
    async with get_session() as s:
        s.add(row)
        await s.commit()
        await s.refresh(row)

    record_audit(
        user.id, "api_key.create",
        target_type="api_key", target_id=str(row.id),
        workspace_id=workspace_uuid,
        metadata={"name": row.name, "prefix": row.prefix, "scopes": row.scopes},
    )
    return ApiKeyCreateResponse(
        key=_to_public(row),
        plaintext=plaintext,
    )


@router.get(
    "/keys",
    response_model=list[ApiKeyPublic],
    summary="List API keys owned by the current user",
)
async def list_keys(user: User = Depends(require_user)) -> list[ApiKeyPublic]:
    _require_neon()
    async with get_session() as s:
        rows = (await s.execute(
            select(ApiKey)
            .where(ApiKey.user_id == user.id)
            .order_by(ApiKey.created_at.desc())
        )).scalars().all()
    return [_to_public(r) for r in rows]


@router.delete(
    "/keys/{key_id}",
    summary="Revoke an API key (soft-delete; takes effect within ~5 min)",
)
async def revoke_key(
    key_id: str,
    user: User = Depends(require_user),
) -> dict:
    _require_neon()
    try:
        kid = uuid.UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="key_id must be a UUID.")

    async with get_session() as s:
        row = (await s.execute(
            select(ApiKey).where(ApiKey.id == kid)
        )).scalar_one_or_none()
        if row is None or row.user_id != user.id:
            # 404 (not 403) — same tenant-isolation rule we use elsewhere.
            raise HTTPException(status_code=404, detail="API key not found.")
        if row.revoked_at is not None:
            return {"ok": True, "revoked_key_id": key_id, "already_revoked": True}
        await s.execute(
            update(ApiKey)
            .where(ApiKey.id == kid)
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await s.commit()
        prefix = row.prefix

    # Drop the cache entry immediately so the next call from this key
    # fails fast. Other workers pick it up on TTL expiry (5 min).
    invalidate_api_key_cache(prefix)

    record_audit(
        user.id, "api_key.revoke",
        target_type="api_key", target_id=key_id,
    )
    return {"ok": True, "revoked_key_id": key_id}
