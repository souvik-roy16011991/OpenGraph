"""
Neon Auth JWT verification.

Responsibilities:
  - Fetch the Neon Auth JWKS (cached 24h in Upstash, in-proc fallback) and
    refetch on-demand when a JWT's ``kid`` is absent (key rotation).
  - Validate incoming ``Authorization: Bearer <jwt>`` tokens with issuer,
    audience, algorithm, and signature checks.
  - Upsert the matching ``User`` row in Neon (``stack_user_id`` column stores
    Neon Auth's ``sub``). A small in-proc LRU keeps the steady-state hot path
    SELECT-free so workers scale horizontally.
  - Expose two FastAPI deps:
      * ``current_user()`` — optional; returns ``None`` when no token is
        present OR when auth isn't configured.
      * ``require_user()`` — strict; 401 if not authenticated, 503 if auth
        is not configured on this deployment.

Design notes:
  - JWKS fetch is best-effort; a failure returns None so the app continues
    to serve unauth traffic during outages. We never 500 because the Neon
    Auth JWKS endpoint is unreachable.
  - Verification uses the JWKS 'kid' header claim to pick the right key. If
    the kid isn't in the cached JWKS we invalidate the cache and refetch
    once — this makes a key rotation recover in the time of one httpx.get
    rather than waiting out the 24h TTL.
  - We never trust any claims beyond what python-jose verifies
    cryptographically (iss/aud/exp/signature).
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Any, Optional

import httpx
from fastapi import Header, HTTPException
from sqlalchemy import select

from src.config import (
    NEON_AUTH_AUDIENCE,
    NEON_AUTH_ISSUER,
    NEON_AUTH_JWKS_URL,
    NEON_AUTH_JWT_LEEWAY_SECONDS,
    NEON_AUTH_PROJECT_ID,
    USE_NEON,
    USE_NEON_AUTH,
)
from src.infra import upstash
from src.infra.db import get_session
from src.infra.db_models import User

logger = logging.getLogger(__name__)

_JWKS_CACHE_KEY = lambda: f"jwks:neon-auth:{NEON_AUTH_PROJECT_ID}"  # noqa: E731
_JWKS_TTL_SECONDS = 24 * 3600

# In-proc LRU for sub -> User row. Verified JWTs still decode every request;
# this only skips the SELECT/UPDATE when we've already upserted this user
# recently. 5-minute TTL is well under the typical JWT expiry, so staleness
# is bounded by the token refresh cycle rather than this cache.
_USER_CACHE_TTL_SECONDS = 300
_USER_CACHE_MAX = 1024
_user_cache: dict[str, tuple[float, User]] = {}


# ---------------------------------------------------------------------------
# JWKS fetch + cache
# ---------------------------------------------------------------------------

def _jwks_url() -> str:
    return NEON_AUTH_JWKS_URL or ""


@lru_cache(maxsize=1)
def _fallback_jwks() -> dict[str, Any] | None:
    """In-proc fallback for JWKS in case Upstash is unreachable."""
    return None


def _fetch_jwks_remote() -> Optional[dict[str, Any]]:
    """Fetch the JWKS JSON from Neon Auth. Returns None on any error."""
    url = _jwks_url()
    if not url:
        return None
    try:
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Neon Auth JWKS fetch failed: %s", exc)
        return None


def get_jwks(force_refresh: bool = False) -> Optional[dict[str, Any]]:
    """Return the current JWKS. Upstash-cached; on ``force_refresh`` skip the
    cache and repopulate it. Used by ``_verify_jwt`` when a token's ``kid`` is
    missing from the cached keys (i.e. after a key rotation).
    """
    if not USE_NEON_AUTH:
        return None
    if not force_refresh:
        cached = upstash.get_json(_JWKS_CACHE_KEY())
        if isinstance(cached, dict) and cached.get("keys"):
            return cached
    fresh = _fetch_jwks_remote()
    if fresh:
        upstash.set_json(_JWKS_CACHE_KEY(), fresh, ttl_seconds=_JWKS_TTL_SECONDS)
        return fresh
    # Last-resort fallback (e.g., first startup, Upstash down, JWKS down).
    return _fallback_jwks()


# ---------------------------------------------------------------------------
# JWT verification
# ---------------------------------------------------------------------------

def _verify_jwt(token: str) -> Optional[dict[str, Any]]:
    """Return the decoded+verified JWT claims, or None on any error.

    A failure here must never crash a request — we log and return None so the
    caller (current_user) treats it the same as "no header sent".
    """
    if not token or not USE_NEON_AUTH:
        return None
    try:
        from jose import jwt
        from jose.exceptions import JWTError
    except ImportError:
        logger.error("python-jose is not installed; cannot verify Neon Auth JWTs.")
        return None

    try:
        header = jwt.get_unverified_header(token)
    except Exception as exc:
        logger.info("Neon Auth JWT header parse failed: %s", exc)
        return None
    kid = header.get("kid")

    jwks = get_jwks()
    key = _pick_key(jwks, kid) if jwks else None
    # If the token advertises a kid we don't know about, the key set probably
    # rotated since we last cached it. Drop the cache and refetch once.
    if key is None and kid:
        jwks = get_jwks(force_refresh=True)
        key = _pick_key(jwks, kid) if jwks else None
    if key is None:
        logger.warning("Neon Auth JWT kid=%r not found in JWKS (post-refresh).", kid)
        return None

    alg = key.get("alg")
    if not alg or alg.lower() == "none":
        logger.warning("Neon Auth JWKS key %r has unusable alg=%r.", kid, alg)
        return None

    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=[alg],
            issuer=NEON_AUTH_ISSUER or None,
            audience=NEON_AUTH_AUDIENCE or None,
            options={
                "verify_aud": bool(NEON_AUTH_AUDIENCE),
                "leeway": NEON_AUTH_JWT_LEEWAY_SECONDS,
            },
        )
        return claims
    except JWTError as exc:
        logger.info("Neon Auth JWT verification failed: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Neon Auth JWT decode raised: %s", exc)
        return None


def _pick_key(jwks: Optional[dict[str, Any]], kid: Optional[str]) -> Optional[dict[str, Any]]:
    if not jwks:
        return None
    keys = jwks.get("keys") or []
    if kid:
        for k in keys:
            if k.get("kid") == kid:
                return k
        return None
    return keys[0] if keys else None


# ---------------------------------------------------------------------------
# User upsert (+ in-proc cache)
# ---------------------------------------------------------------------------

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
        # Cheap eviction: drop the oldest entry. A proper LRU is overkill for
        # a per-process cache with O(max=1024) entries.
        oldest = min(_user_cache.items(), key=lambda kv: kv[1][0])[0]
        _user_cache.pop(oldest, None)
    _user_cache[sub] = (time.monotonic(), user)


async def _upsert_user(claims: dict[str, Any]) -> Optional[User]:
    """Given verified JWT claims, return the matching User row (creating on
    first sight). Returns None when Neon is off or the claim set is unusable.
    """
    if not USE_NEON:
        return None
    sub = claims.get("sub")
    if not sub:
        return None
    cached = _cache_get(sub)
    if cached is not None:
        return cached

    email = claims.get("email")
    display = claims.get("name") or claims.get("display_name") or email
    async with get_session() as s:
        existing = (await s.execute(
            select(User).where(User.stack_user_id == sub)
        )).scalar_one_or_none()
        if existing is not None:
            changed = False
            if email and existing.email != email:
                existing.email = email
                changed = True
            if display and existing.display_name != display:
                existing.display_name = display
                changed = True
            if changed:
                await s.commit()
            _cache_put(sub, existing)
            return existing
        user = User(stack_user_id=sub, email=email, display_name=display)
        s.add(user)
        await s.commit()
        await s.refresh(user)
        try:
            from src.infra.audit import record_audit
            record_audit(
                user.id, "auth.signup",
                target_type="user", target_id=str(user.id),
                metadata={"email": email, "stack_user_id": sub},
            )
        except Exception:
            pass
        _cache_put(sub, user)
        return user


# ---------------------------------------------------------------------------
# FastAPI deps
# ---------------------------------------------------------------------------

async def current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Optional[User]:
    """Return the caller's ``User`` row if a valid Bearer JWT is sent."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    claims = _verify_jwt(token)
    if not claims:
        return None
    try:
        return await _upsert_user(claims)
    except Exception as exc:
        logger.warning("User upsert failed (claims sub=%s): %s", claims.get("sub"), exc)
        return None


async def require_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> User:
    """Return the caller's User row. 401 if no/invalid token; 503 if auth
    isn't configured on this deployment.

    There is no dev-mode / anonymous fallback: Neon Auth must be wired up
    for protected routes to respond at all.
    """
    if not USE_NEON_AUTH:
        raise HTTPException(
            status_code=503,
            detail="Authentication is not configured on this deployment.",
        )
    user = await current_user(authorization)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user
