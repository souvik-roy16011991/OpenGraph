"""
Developer API-key authentication for the /api/v1/ext/* surface.

Layered on top of the JWT auth in ``src.api.auth`` — same bearer header,
different secret format. Keys look like ``og_live_<32-char-base64urlsafe>``;
the ``og_live_`` prefix distinguishes them from JWTs (eyJ…) so the same
``Authorization: Bearer`` header can carry either.

Hot-path flow:

    Authorization: Bearer og_live_…
    ─► _extract_api_key()          parse prefix + secret
    ─► _cache_get()                in-proc LRU (5 min TTL)
    ─► _load_key_from_db()         fallback Neon SELECT by prefix (partial index)
    ─► bcrypt verify               constant-time hash check
    ─► update last_used_at         fire-and-forget, doesn't block the request
    ─► return ApiKeyContext        user_id, workspace_id, key_id, scopes

The cache eliminates the bcrypt + DB round-trip after the first use of a
key; a 5-min TTL means a revocation is effective within that window.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select, update

from src.infra.db import fire_and_forget, get_session
from src.infra.db_models import ApiKey

logger = logging.getLogger(__name__)

_KEY_MARKER = "og_live_"
_PREFIX_LEN = len(_KEY_MARKER) + 8  # og_live_ + first 8 chars of the secret


@dataclass(frozen=True)
class ApiKeyContext:
    """The verified identity for a /ext/* request.

    ``workspace_id`` is Optional — an account-scoped key (workspace_id=None
    at creation) can target any workspace owned by ``user_id``; the ext
    routes still verify ownership on every call. A workspace-scoped key
    pins one workspace and any request referencing a different workspace
    id returns 404.
    """
    key_id: uuid.UUID
    user_id: uuid.UUID
    workspace_id: Optional[uuid.UUID]
    scopes: list[str]
    rate_limit_rpm: int


# ---------------------------------------------------------------------------
# Cache (prefix -> (issued_at, ApiKeyContext))
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS = int(os.environ.get("API_KEY_CACHE_TTL_SECONDS", "300"))
# 8192 distinct keys per worker is ample — a user with 100 keys making
# 100 RPS each fits in-cache forever, and cold starts only cost one
# bcrypt verify + SELECT per key.
_CACHE_MAX = int(os.environ.get("API_KEY_CACHE_MAX", "8192"))
_cache: dict[str, tuple[float, ApiKeyContext]] = {}


def _cache_get(prefix: str) -> Optional[ApiKeyContext]:
    entry = _cache.get(prefix)
    if entry is None:
        return None
    ts, ctx = entry
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        _cache.pop(prefix, None)
        return None
    return ctx


def _cache_put(prefix: str, ctx: ApiKeyContext) -> None:
    if len(_cache) >= _CACHE_MAX:
        oldest = min(_cache.items(), key=lambda kv: kv[1][0])[0]
        _cache.pop(oldest, None)
    _cache[prefix] = (time.monotonic(), ctx)


def invalidate_api_key_cache(prefix: Optional[str] = None) -> None:
    """Drop a single key (or the whole cache) — call on revoke / update."""
    if prefix is None:
        _cache.clear()
    else:
        _cache.pop(prefix, None)


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

def _extract_api_key(authorization: Optional[str]) -> Optional[str]:
    """Pull ``og_live_…`` out of an Authorization: Bearer header.

    Returns None for any header that isn't an API key — including valid
    JWTs, which start with ``eyJ`` not ``og_live_``. This lets the same
    header carry either credential type and lets the caller decide
    which auth dep to invoke.
    """
    if not authorization:
        return None
    if not authorization.lower().startswith("bearer "):
        return None
    raw = authorization[7:].strip()
    if not raw.startswith(_KEY_MARKER):
        return None
    return raw


def _prefix_of(plaintext: str) -> str:
    """Slice the first PREFIX_LEN chars — this is the indexed lookup key."""
    return plaintext[:_PREFIX_LEN]


# ---------------------------------------------------------------------------
# DB path
# ---------------------------------------------------------------------------

async def _load_key_from_db(prefix: str) -> Optional[ApiKey]:
    """Look up a live (non-revoked) key row by prefix.

    Hits the partial index ``ix_api_keys_prefix_live`` (prefix + revoked_at
    IS NULL), so a prefix collision between a live and a revoked key
    doesn't slow this down.
    """
    async with get_session() as s:
        r = (await s.execute(
            select(ApiKey).where(
                ApiKey.prefix == prefix,
                ApiKey.revoked_at.is_(None),
            )
        )).scalar_one_or_none()
        return r


async def _touch_last_used(key_id: uuid.UUID) -> None:
    """Update ``last_used_at`` in a background task. Errors are swallowed
    — this is telemetry, not correctness."""
    try:
        async with get_session() as s:
            await s.execute(
                update(ApiKey)
                .where(ApiKey.id == key_id)
                .values(last_used_at=datetime.now(timezone.utc))
            )
            await s.commit()
    except Exception as exc:
        logger.debug("api_key last_used_at update failed for %s: %s", key_id, exc)


# ---------------------------------------------------------------------------
# Public dependency
# ---------------------------------------------------------------------------

_UNAUTH_DETAIL = "Invalid or missing API key. Use Authorization: Bearer og_live_…"


async def require_api_key(
    authorization: Optional[str] = Header(default=None),
) -> ApiKeyContext:
    """FastAPI dependency — require a valid API key on the request.

    Accepts ``Authorization: Bearer og_live_<secret>`` only. Any other
    bearer value (JWT, missing header, wrong marker) raises 401 so a
    caller wiring this dep in place of ``require_user`` gets a clear
    error instead of silent anonymous access.
    """
    plaintext = _extract_api_key(authorization)
    if plaintext is None:
        raise HTTPException(status_code=401, detail=_UNAUTH_DETAIL)

    prefix = _prefix_of(plaintext)
    cached = _cache_get(prefix)
    if cached is not None:
        # Fire-and-forget last_used bump — don't await; don't want to pay
        # a round-trip on the hot path.
        fire_and_forget(_touch_last_used(cached.key_id))
        return cached

    row = await _load_key_from_db(prefix)
    if row is None:
        raise HTTPException(status_code=401, detail=_UNAUTH_DETAIL)

    # Constant-time bcrypt compare — plaintext must match the stored hash.
    try:
        ok = bcrypt.checkpw(plaintext.encode("utf-8"), row.key_hash.encode("utf-8"))
    except Exception as exc:
        logger.warning("bcrypt check failed for key prefix=%s: %s", prefix, exc)
        raise HTTPException(status_code=401, detail=_UNAUTH_DETAIL)
    if not ok:
        raise HTTPException(status_code=401, detail=_UNAUTH_DETAIL)

    ctx = ApiKeyContext(
        key_id=row.id,
        user_id=row.user_id,
        workspace_id=row.workspace_id,
        scopes=list(row.scopes or []),
        rate_limit_rpm=int(row.rate_limit_rpm or 60),
    )
    _cache_put(prefix, ctx)
    fire_and_forget(_touch_last_used(row.id))
    return ctx


def require_scope(*required: str):
    """Sugar for routes that need a specific scope. Not used in v1 (all
    ext routes use the default ``ext:read``), but wired up so new scopes
    can be enforced with a single decorator change.
    """
    required_set = set(required)

    async def _dep(ctx: ApiKeyContext = Depends(require_api_key)) -> ApiKeyContext:
        if not required_set.issubset(set(ctx.scopes)):
            raise HTTPException(
                status_code=403,
                detail=f"API key missing required scope(s): {', '.join(sorted(required_set))}",
            )
        return ctx

    return _dep
