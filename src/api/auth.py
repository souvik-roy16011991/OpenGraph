"""
First-party JWT auth.

Simple email + password signup/login that issues an HS256 JWT. The token
carries the user's ``id`` (UUID) and ``email`` as claims — each email maps
to exactly one ``User`` row, which is the tenant boundary used by every
downstream query in Neon, Memgraph, and Qdrant.

Public surface (used by FastAPI routes):
    hash_password(plain) / verify_password(plain, hash)
    create_access_token(user) -> str
    current_user   — optional dep; None when no token or not configured.
    require_user   — strict; 401 if bad/missing token, 503 if JWT_SECRET unset.

Scalability:
    - Stateless JWT verify — no DB round-trip to authorize a request.
    - A tiny in-proc LRU (``sub`` -> ``User``) keeps steady-state SELECT-free.
    - Sessions don't live anywhere on the server; rotating ``JWT_SECRET``
      is the one-shot logout-everyone lever.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Header, HTTPException
from sqlalchemy import select

from src.config import (
    JWT_ALGORITHM,
    JWT_EXPIRES_MINUTES,
    JWT_ISSUER,
    JWT_SECRET,
    USE_AUTH,
    USE_NEON,
)
from src.infra.db import get_session
from src.infra.db_models import User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def normalize_email(email: str) -> str:
    """Lowercase + strip whitespace. The canonical form stored in ``users.email``.

    Shared between password signup (``auth_routes``) and OAuth signup
    (``oauth_github``) so both paths collapse ``Foo@x.com`` to ``foo@x.com``
    and hit the same unique-index row.
    """
    return email.strip().lower()


# ---------------------------------------------------------------------------
# Password hashing (bcrypt)
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Return a bcrypt hash for ``plain`` suitable for storing in the DB."""
    import bcrypt
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: Optional[str]) -> bool:
    if not hashed:
        return False
    import bcrypt
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT encode / decode (PyJWT)
# ---------------------------------------------------------------------------

def create_access_token(user: User) -> str:
    """Sign a JWT for ``user``. Claims: sub=user.id, email, iat, exp, iss."""
    if not JWT_SECRET:
        raise RuntimeError("JWT_SECRET is not configured; cannot mint tokens.")
    import jwt
    now = datetime.now(tz=timezone.utc)
    payload = {
        "sub": str(user.id),
        "email": user.email,
        "iss": JWT_ISSUER,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=JWT_EXPIRES_MINUTES)).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _decode_token(token: str) -> Optional[dict[str, Any]]:
    """Return verified claims, or ``None`` if the token is missing/invalid."""
    if not token or not JWT_SECRET:
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
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"require": ["exp", "iat", "sub"]},
        )
    except InvalidTokenError as exc:
        logger.info("JWT verification failed: %s", exc)
        return None
    except Exception as exc:
        logger.warning("JWT decode raised: %s", exc)
        return None


# ---------------------------------------------------------------------------
# In-proc user cache (sub -> User)
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
    """Drop a single user (or the whole cache) — used on profile updates."""
    if user_id is None:
        _user_cache.clear()
    else:
        _user_cache.pop(str(user_id), None)


# ---------------------------------------------------------------------------
# User lookup
# ---------------------------------------------------------------------------

async def _load_user(sub: str) -> Optional[User]:
    """Load the ``User`` row for the JWT subject. Cached in-proc for 5 min."""
    if not USE_NEON:
        return None
    cached = _cache_get(sub)
    if cached is not None:
        return cached
    import uuid
    try:
        user_uuid = uuid.UUID(sub)
    except (ValueError, TypeError):
        return None
    async with get_session() as s:
        row = (await s.execute(
            select(User).where(User.id == user_uuid)
        )).scalar_one_or_none()
    if row is not None:
        _cache_put(sub, row)
    return row


async def get_user_by_email(email: str) -> Optional[User]:
    """Return the ``User`` row for a given email (case-sensitive) or None."""
    if not USE_NEON:
        return None
    async with get_session() as s:
        return (await s.execute(
            select(User).where(User.email == email)
        )).scalar_one_or_none()


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
    """Return the caller's ``User`` row if a valid Bearer JWT is sent."""
    token = _extract_bearer(authorization)
    if not token:
        return None
    claims = _decode_token(token)
    if not claims:
        return None
    sub = claims.get("sub")
    if not sub:
        return None
    try:
        return await _load_user(sub)
    except Exception as exc:
        logger.warning("User lookup failed (sub=%s): %s", sub, exc)
        return None


async def require_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> User:
    """Return the caller's ``User`` row. 401 if no/invalid token; 503 if auth
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
