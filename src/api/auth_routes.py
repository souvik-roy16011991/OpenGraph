"""
Login / logout endpoints.

    POST /api/v1/auth/login   { email, password }   -> { token, user }
    POST /api/v1/auth/logout                          -> { ok: true }

The token returned is an HS256 JWT (see ``src.api.auth``). Clients should
send it back in ``Authorization: Bearer <token>`` on protected routes.

Signup lives in ``src.api.signup_otp`` — email/password signup is gated
behind a 6-digit OTP delivered via Resend. GitHub OAuth signup is in
``src.api.oauth_github`` and is not gated.

Logout is a no-op server-side — JWTs are stateless. Clients drop the token
locally; rotating ``JWT_SECRET`` is the lever to invalidate everyone at once.

Tenant isolation: each email maps to exactly one ``User`` row; every
downstream query scopes by ``User.id``.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from src.api.auth import (
    create_access_token,
    get_user_by_email,
    invalidate_user_cache,
    normalize_email,
    require_user,
    verify_password,
)
from src.config import USE_AUTH, USE_NEON
from src.infra.audit import record_audit
from src.infra.db_models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

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


def _user_payload(user: User) -> UserPayload:
    return UserPayload(
        id=str(user.id),
        email=user.email or "",
        display_name=user.display_name,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/login", response_model=TokenResponse, summary="Log in with email + password")
async def login(body: LoginRequest) -> TokenResponse:
    _guard()
    email = normalize_email(body.email)
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
