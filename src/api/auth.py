"""
Supabase Auth integration — JWT verification + lazy user sync.

Supabase issues HS256 access tokens signed with the project JWT secret
(SUPABASE_JWT_SECRET). The backend verifies the signature on every protected
request; Supabase handles signup, login, token refresh, password reset, and
OAuth (Google, GitHub).

Token claims relevant here:
    sub          — Supabase user UUID (becomes our User.id)
    email        — user's email address
    aud          — always "authenticated" for logged-in users
    user_metadata — dict that may contain display_name set at signup

User sync (lazy upsert):
    Supabase manages auth.users internally. Our public.users table is
    populated on the first authenticated API request: if a row for the JWT
    sub doesn't exist, one is created from the JWT claims. Subsequent
    requests hit the in-proc LRU cache (5-minute TTL, up to 1024 entries).

Public surface (used by FastAPI routes):
    current_user   — optional dep; None when no/invalid token.
    require_user   — strict dep; 401 if bad/missing, 503 if secret unset.
    invalidate_user_cache(user_id) — bust the cache on profile update.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional

from fastapi import Header, HTTPException
from sqlalchemy import select

from src.config import SUPABASE_JWT_SECRET, USE_AUTH, USE_NEON
from src.infra.db import get_session
from src.infra.db_models import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JWT decode (Supabase HS256)
# ---------------------------------------------------------------------------

def _decode_token(token: str) -> Optional[dict[str, Any]]:
    """Verify the Supabase access token and return its claims, or None."""
    if not token or not SUPABASE_JWT_SECRET:
        return None
    try:
        import jwt
        from jwt import InvalidTokenError
    except ImportError:
        logger.error("PyJWT is not installed; cannot verify JWTs.")
        return None
    try:
        return jwt.decode(
            token,
            SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
            options={"require": ["exp", "sub"]},
        )
    except InvalidTokenError as exc:
        logger.info("JWT verification failed: %s", exc)
        return None
    except Exception as exc:
        logger.warning("JWT decode raised: %s", exc)
        return None


# ---------------------------------------------------------------------------
# In-proc user cache (sub -> User, 5-minute TTL)
# ---------------------------------------------------------------------------

_USER_CACHE_TTL_SECONDS = 300
_USER_CACHE_MAX = 1024
_user_cache: dict[str, tuple[float, User]] = {}


def _cache_get(sub: str) -> Optional[User]:
    entry = _user_cache.get(sub)
    if not entry:
        return None
    ts, user = entry
    if time.monotonic() - ts > _USER_CACHE_TTL_SECONDS:
        _user_cache.pop(sub, None)
        return None
    return user


def _cache_put(sub: str, user: User) -> None:
    if len(_user_cache) >= _USER_CACHE_MAX:
        oldest = min(_user_cache.items(), key=lambda kv: kv[1][0])[0]
        _user_cache.pop(oldest, None)
    _user_cache[sub] = (time.monotonic(), user)


def invalidate_user_cache(user_id: Optional[str] = None) -> None:
    """Drop one user entry (or the whole cache) — call on profile updates."""
    if user_id is None:
        _user_cache.clear()
    else:
        _user_cache.pop(str(user_id), None)


# ---------------------------------------------------------------------------
# User lookup + lazy upsert
# ---------------------------------------------------------------------------

async def _load_user(
    sub: str,
    email: Optional[str],
    display_name: Optional[str],
) -> Optional[User]:
    """Return the User row for sub, creating it on first sight (lazy sync)."""
    if not USE_NEON:
        return None
    cached = _cache_get(sub)
    if cached is not None:
        return cached
    try:
        user_uuid = uuid.UUID(sub)
    except (ValueError, TypeError):
        return None
    async with get_session() as s:
        row = (await s.execute(
            select(User).where(User.id == user_uuid)
        )).scalar_one_or_none()
        if row is None:
            row = User(id=user_uuid, email=email, display_name=display_name)
            s.add(row)
            await s.commit()
            await s.refresh(row)
    _cache_put(sub, row)
    return row


# ---------------------------------------------------------------------------
# FastAPI deps
# ---------------------------------------------------------------------------

def _extract_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


async def current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Optional[User]:
    """Return the caller's User row if a valid Supabase Bearer token is sent."""
    token = _extract_bearer(authorization)
    if not token:
        return None
    claims = _decode_token(token)
    if not claims:
        return None
    sub = claims.get("sub")
    if not sub:
        return None
    email: Optional[str] = claims.get("email")
    meta: dict = claims.get("user_metadata") or {}
    display_name: Optional[str] = meta.get("display_name") or meta.get("full_name")
    try:
        return await _load_user(sub, email, display_name)
    except Exception as exc:
        logger.warning("User lookup failed (sub=%s): %s", sub, exc)
        return None


async def require_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> User:
    """Return the caller's User row. 401 if no/invalid token; 503 if auth
    isn't configured on this deployment.
    """
    if not USE_AUTH:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured on this deployment.",
        )
    user = await current_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user
