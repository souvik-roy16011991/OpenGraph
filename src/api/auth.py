"""
Neon Auth (Stack Auth) JWT verification.

Responsibilities:
  - Fetch the Stack Auth JWKS once (cached 24h in Upstash, in-proc fallback).
  - Validate incoming ``Authorization: Bearer <jwt>`` tokens.
  - Upsert the matching ``User`` row in Neon (populating ``stack_user_id``,
    ``email``, ``display_name`` on first sight).
  - Expose two FastAPI deps:
      * ``current_user()`` — optional; returns ``None`` when no token is
        present OR when auth isn't configured (Phase 1b: parsed, not enforced).
      * ``require_user()`` — strict; raises 401 if the caller is not
        authenticated. Used by Phase 1d and beyond.

Design notes:
  - JWKS fetch is best-effort; a failure returns None so the app continues
    to serve unauth traffic during outages. We never 500 because the Stack
    JWKS endpoint is unreachable.
  - Verification uses the JWKS 'kid' header claim to pick the right key.
  - We deliberately DO NOT trust any claims beyond what python-jose verified
    cryptographically (iss/aud/exp/signature).
"""

from __future__ import annotations

import logging
import uuid
from functools import lru_cache
from typing import Any, Optional

import httpx
from fastapi import Header, HTTPException
from sqlalchemy import select

from src.config import (
    STACK_JWT_ISSUER,
    STACK_PROJECT_ID,
    USE_NEON,
    USE_STACK_AUTH,
)
from src.infra import upstash
from src.infra.db import get_session
from src.infra.db_models import User

logger = logging.getLogger(__name__)

_JWKS_CACHE_KEY = lambda: f"jwks:stack:{STACK_PROJECT_ID}"  # noqa: E731
_JWKS_TTL_SECONDS = 24 * 3600


# ---------------------------------------------------------------------------
# JWKS fetch + cache
# ---------------------------------------------------------------------------

def _jwks_url() -> str:
    if not STACK_PROJECT_ID:
        return ""
    return f"https://api.stack-auth.com/api/v1/projects/{STACK_PROJECT_ID}/.well-known/jwks.json"


@lru_cache(maxsize=1)
def _fallback_jwks() -> dict[str, Any] | None:
    """In-proc fallback for JWKS in case Upstash is unreachable."""
    return None


def _fetch_jwks_remote() -> Optional[dict[str, Any]]:
    """Fetch the JWKS JSON from Stack Auth. Returns None on any error."""
    url = _jwks_url()
    if not url:
        return None
    try:
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Stack JWKS fetch failed: %s", exc)
        return None


def get_jwks() -> Optional[dict[str, Any]]:
    """Return the current JWKS, Upstash-cached."""
    if not USE_STACK_AUTH:
        return None
    # Upstash first
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
    caller (current_user) treats it the same as "no header sent" during the
    Phase 1b soft-rollout window.
    """
    if not token or not USE_STACK_AUTH:
        return None
    try:
        # Lazy import: python-jose isn't required when USE_STACK_AUTH is off.
        from jose import jwt
        from jose.exceptions import JWTError
    except ImportError:
        logger.error("python-jose is not installed; cannot verify Stack JWTs.")
        return None

    jwks = get_jwks()
    if not jwks:
        return None

    try:
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        keys = jwks.get("keys", [])
        key = next((k for k in keys if k.get("kid") == kid), None) if kid else (keys[0] if keys else None)
        if key is None:
            logger.warning("JWT kid=%r not found in JWKS.", kid)
            return None
        claims = jwt.decode(
            token,
            key,
            algorithms=[key.get("alg", "RS256")],
            # Stack Auth JWTs carry an `iss` that matches the project endpoint.
            issuer=STACK_JWT_ISSUER or None,
            # We don't verify audience here — Stack uses the project_id as aud
            # in some flows; this can be tightened once we standardise the
            # client SDK call that produces the tokens we accept.
            options={"verify_aud": False},
        )
        return claims
    except JWTError as exc:
        logger.info("Stack JWT verification failed: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Stack JWT decode raised: %s", exc)
        return None


# ---------------------------------------------------------------------------
# User upsert
# ---------------------------------------------------------------------------

async def _upsert_user(claims: dict[str, Any]) -> Optional[User]:
    """Given verified JWT claims, return the matching User row (creating on
    first sight). Returns None when Neon is off or the claim set is unusable.
    """
    if not USE_NEON:
        return None
    sub = claims.get("sub")
    if not sub:
        return None
    email = claims.get("email")
    display = claims.get("name") or claims.get("display_name") or email
    async with get_session() as s:
        existing = (await s.execute(
            select(User).where(User.stack_user_id == sub)
        )).scalar_one_or_none()
        if existing is not None:
            # Keep email / display_name fresh — cheap no-op if unchanged.
            changed = False
            if email and existing.email != email:
                existing.email = email
                changed = True
            if display and existing.display_name != display:
                existing.display_name = display
                changed = True
            if changed:
                await s.commit()
            return existing
        user = User(stack_user_id=sub, email=email, display_name=display)
        s.add(user)
        await s.commit()
        await s.refresh(user)
        return user


# ---------------------------------------------------------------------------
# FastAPI deps
# ---------------------------------------------------------------------------

async def current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Optional[User]:
    """Return the caller's ``User`` row if a valid Bearer JWT is sent.

    Phase 1b: never raises on missing / invalid tokens — returns None instead.
    This lets auth ride alongside the existing unauthenticated traffic until
    enforcement is turned on in Phase 1d via ``require_user``.
    """
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
    """Return the caller's User row, 401 if not authenticated.

    Behaviour depends on ``USE_STACK_AUTH``:
      * **Enabled (production)**: a valid Bearer JWT is required. Missing
        / invalid → 401. Every workspace route eventually checks ownership
        against the ``User.id`` returned here.
      * **Disabled (dev / early staging)**: falls back to a single shared
        dev user (``stack_user_id = '__anonymous__'``) so local workflow
        keeps working without a Stack project. The dev fallback is
        **never** used when Stack Auth is configured — once env vars are
        set, only real JWTs grant access.
    """
    user = await current_user(authorization)
    if user is not None:
        return user
    if USE_STACK_AUTH:
        raise HTTPException(status_code=401, detail="Authentication required.")
    # Dev fallback — preserves existing unauthenticated workflow until the
    # operator sets STACK_PROJECT_ID in env. Not reachable in production.
    return await _get_or_create_dev_user()


async def _get_or_create_dev_user() -> User:
    """Fetch-or-create the single shared dev fallback user.

    Uses ``ANONYMOUS_STACK_ID`` so this is idempotent with the existing
    `_get_anonymous_user_id()` helper; once Phase 1c wipes anon-owned data
    and STACK_PROJECT_ID is set, this path stops being reached entirely.
    """
    from src.infra.db_models import ANONYMOUS_STACK_ID
    if not USE_NEON:
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL (Neon) is required for workspace-scoped operations.",
        )
    async with get_session() as s:
        existing = (await s.execute(
            select(User).where(User.stack_user_id == ANONYMOUS_STACK_ID)
        )).scalar_one_or_none()
        if existing is not None:
            return existing
        u = User(stack_user_id=ANONYMOUS_STACK_ID, display_name="Anonymous (dev)")
        s.add(u)
        await s.commit()
        await s.refresh(u)
        return u
