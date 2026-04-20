"""
Signup / login / logout endpoints.

    POST /api/v1/auth/signup  { email, password, display_name? } -> { token, user }
    POST /api/v1/auth/login   { email, password }                -> { token, user }
    POST /api/v1/auth/logout                                      -> { ok: true }

The token returned is an HS256 JWT (see ``src.api.auth``). Clients should
send it back in ``Authorization: Bearer <token>`` on protected routes.

Logout is a no-op server-side — JWTs are stateless. Clients drop the token
locally; rotating ``JWT_SECRET`` is the lever to invalidate everyone at once.

Tenant isolation: each email maps to exactly one ``User`` row; every
downstream query scopes by ``User.id``. Duplicate-email signup returns 409.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from src.api.auth import (
    create_access_token,
    get_user_by_email,
    hash_password,
    invalidate_user_cache,
    require_user,
    verify_password,
)
from src.config import PASSWORD_MIN_LENGTH, USE_AUTH, USE_NEON
from src.infra.audit import record_audit
from src.infra.db import get_session
from src.infra.db_models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=256)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class UserPayload(BaseModel):
    id: str
    email: str
    display_name: Optional[str]


class TokenResponse(BaseModel):
    token: str
    token_type: str = "bearer"
    user: UserPayload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guard() -> None:
    if not USE_AUTH:
        raise HTTPException(status_code=503, detail="JWT_SECRET is not configured.")
    if not USE_NEON:
        raise HTTPException(status_code=503, detail="DATABASE_URL (Neon) is required for auth.")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _user_payload(user: User) -> UserPayload:
    return UserPayload(
        id=str(user.id),
        email=user.email or "",
        display_name=user.display_name,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/signup", response_model=TokenResponse, summary="Create a new account")
async def signup(body: SignupRequest) -> TokenResponse:
    _guard()
    if len(body.password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {PASSWORD_MIN_LENGTH} characters.",
        )

    email = _normalize_email(body.email)
    # Case-insensitive duplicate check — prevents `Foo@x.com` vs `foo@x.com`
    # collisions.
    existing = await get_user_by_email(email)
    if existing is not None:
        raise HTTPException(status_code=409, detail="An account with that email already exists.")

    display = body.display_name or email.split("@", 1)[0]
    pw_hash = hash_password(body.password)

    async with get_session() as s:
        user = User(email=email, display_name=display, password_hash=pw_hash)
        s.add(user)
        try:
            await s.commit()
        except Exception as exc:
            await s.rollback()
            # Race: another signup for the same email raced us to the unique
            # index. Surface as 409 rather than 500.
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                raise HTTPException(status_code=409, detail="An account with that email already exists.")
            logger.exception("signup failed for %s", email)
            raise HTTPException(status_code=500, detail="Signup failed.")
        await s.refresh(user)

    try:
        record_audit(
            user.id, "auth.signup",
            target_type="user", target_id=str(user.id),
            metadata={"email": email},
        )
    except Exception:
        pass

    token = create_access_token(user)
    return TokenResponse(token=token, user=_user_payload(user))


@router.post("/login", response_model=TokenResponse, summary="Log in with email + password")
async def login(body: LoginRequest) -> TokenResponse:
    _guard()
    email = _normalize_email(body.email)
    user = await get_user_by_email(email)
    if user is None or not verify_password(body.password, user.password_hash):
        # Uniform 401 so callers can't tell apart "no such user" vs "wrong pw".
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    try:
        record_audit(
            user.id, "auth.login",
            target_type="user", target_id=str(user.id),
        )
    except Exception:
        pass

    # Drop any cached row so the next request sees fresh profile fields.
    invalidate_user_cache(str(user.id))
    token = create_access_token(user)
    return TokenResponse(token=token, user=_user_payload(user))


@router.post("/logout", summary="Client-side token drop (no-op server-side)")
async def logout(user: User = Depends(require_user)) -> dict:
    # JWT is stateless — we can't revoke a single token without a blocklist.
    # Log the event so audit trails are complete; the client is expected to
    # clear the stored token + cookie itself.
    try:
        record_audit(user.id, "auth.logout", target_type="user", target_id=str(user.id))
    except Exception:
        pass
    return {"ok": True}
