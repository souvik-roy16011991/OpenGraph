"""
Email-OTP gated signup.

Replaces the prior single-shot ``POST /api/v1/auth/signup`` (which created a
``User`` row + minted a JWT in one round-trip) with a three-step flow:

    POST /api/v1/auth/signup           {email,password,display_name?} -> {ticket,...}
    POST /api/v1/auth/signup/verify    {ticket,otp}                   -> {token,user}
    POST /api/v1/auth/signup/resend    {ticket}                       -> {ticket,...}

The pending signup payload (email, display_name, password_hash, otp_hash,
attempts, resends) parks in Upstash Redis under an opaque random ticket id
with a TTL of OTP_TTL_SECONDS. The *id itself is the auth* — no JWT signing
needed, since unknown ids can't be guessed (~256 bits of entropy). Only the
ticket id leaves the backend; the bcrypt password hash and OTP hash never
reach the browser.

The ``User`` row + app JWT are created only after a correct OTP — so until
verification completes there is no DB row to clean up if the user
abandons signup.

GitHub OAuth signup (``src.api.oauth_github``) and email/password login
(``auth_routes.login``) are unaffected by this module.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field

from src.api.auth import (
    create_access_token,
    get_user_by_email,
    hash_password,
    normalize_email,
    verify_password,
)
from src.api.auth_routes import TokenResponse, UserPayload
from src.config import (
    OTP_LENGTH,
    OTP_MAX_ATTEMPTS,
    OTP_MAX_RESENDS,
    OTP_TTL_SECONDS,
    PASSWORD_MIN_LENGTH,
    USE_AUTH,
    USE_NEON,
    USE_RESEND,
    USE_UPSTASH,
)
from src.infra import rate_limit, upstash
from src.infra.audit import record_audit
from src.infra.db import get_session
from src.infra.db_models import User
from src.infra.email import send_otp_email

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)
    display_name: Optional[str] = Field(default=None, min_length=1, max_length=256)


class VerifyRequest(BaseModel):
    ticket: str = Field(min_length=8, max_length=128)
    otp: str = Field(min_length=1, max_length=16)


class ResendRequest(BaseModel):
    ticket: str = Field(min_length=8, max_length=128)


class TicketResponse(BaseModel):
    ticket: str
    email: str
    expires_in: int


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def _guard_signup() -> None:
    if not USE_AUTH:
        raise HTTPException(status_code=503, detail="JWT_SECRET is not configured.")
    if not USE_NEON:
        raise HTTPException(status_code=503, detail="DATABASE_URL (Neon) is required for auth.")
    if not USE_UPSTASH:
        raise HTTPException(status_code=503, detail="Upstash Redis is required for signup OTP.")
    if not USE_RESEND:
        raise HTTPException(status_code=503, detail="Email service is not configured.")


def _guard_verify() -> None:
    # /verify and /resend don't strictly need Resend (resend does for the next email),
    # but they all need the same auth + DB stack.
    if not USE_AUTH:
        raise HTTPException(status_code=503, detail="JWT_SECRET is not configured.")
    if not USE_NEON:
        raise HTTPException(status_code=503, detail="DATABASE_URL (Neon) is required for auth.")
    if not USE_UPSTASH:
        raise HTTPException(status_code=503, detail="Upstash Redis is required for signup OTP.")


# ---------------------------------------------------------------------------
# Ticket / OTP helpers
# ---------------------------------------------------------------------------

_TICKET_PREFIX = "st_"
_REDIS_PREFIX = "signup:ticket:"


def _make_ticket_id() -> str:
    """~256 bits of entropy. Opaque string; the redis lookup itself is the auth."""
    return _TICKET_PREFIX + secrets.token_urlsafe(32)


def _redis_key(ticket_id: str) -> str:
    return f"{_REDIS_PREFIX}{ticket_id}"


def _generate_otp() -> str:
    """Cryptographically uniform N-digit OTP (no modulo bias from randint)."""
    return "".join(secrets.choice("0123456789") for _ in range(OTP_LENGTH))


def _park_ticket(ticket_id: str, payload: dict, ttl_seconds: int) -> bool:
    return upstash.set_json(_redis_key(ticket_id), payload, ttl_seconds=ttl_seconds)


def _load_ticket(ticket_id: str) -> Optional[dict]:
    if not isinstance(ticket_id, str) or not ticket_id.startswith(_TICKET_PREFIX):
        return None
    val = upstash.get_json(_redis_key(ticket_id))
    if not isinstance(val, dict):
        return None
    return val


def _burn_ticket(ticket_id: str) -> None:
    upstash.delete(_redis_key(ticket_id))


def _user_payload(user: User) -> UserPayload:
    return UserPayload(
        id=str(user.id),
        email=user.email or "",
        display_name=user.display_name,
    )


def _enforce_rate_limit(response: Response, scope: str, subject: str, limit: int, message: str) -> None:
    decision = rate_limit.check(scope=scope, subject=subject, limit=limit)
    rate_limit.apply_headers(response, decision)
    if not decision.allowed:
        raise HTTPException(status_code=429, detail=message)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/signup", response_model=TicketResponse, summary="Begin signup; emails an OTP")
async def signup_start(body: SignupRequest, response: Response) -> TicketResponse:
    _guard_signup()

    if len(body.password) < PASSWORD_MIN_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {PASSWORD_MIN_LENGTH} characters.",
        )

    email = normalize_email(body.email)

    # Rate-limit BEFORE the duplicate check so we don't leak a per-email
    # enumeration channel. 3/min/email is generous for typos.
    _enforce_rate_limit(
        response,
        scope="otp_send",
        subject=email,
        limit=3,
        message="Too many signup attempts for this email. Try again in a minute.",
    )

    # Early reject if the user already exists — don't even park a ticket.
    existing = await get_user_by_email(email)
    if existing is not None:
        raise HTTPException(status_code=409, detail="An account with that email already exists.")

    display = body.display_name or email.split("@", 1)[0]
    pw_hash = hash_password(body.password)
    otp = _generate_otp()
    otp_hash = hash_password(otp)

    ticket_id = _make_ticket_id()
    payload = {
        "email": email,
        "display_name": display,
        "password_hash": pw_hash,
        "otp_hash": otp_hash,
        "attempts": 0,
        "resends": 0,
        "created_at": int(time.time()),
    }
    if not _park_ticket(ticket_id, payload, OTP_TTL_SECONDS):
        logger.error("Failed to park signup ticket for %s (Upstash write failed)", email)
        raise HTTPException(status_code=503, detail="Could not start signup right now. Try again.")

    sent = await send_otp_email(email, otp, OTP_TTL_SECONDS)
    if not sent:
        # Burn the ticket so the user isn't stuck on a non-deliverable code.
        _burn_ticket(ticket_id)
        raise HTTPException(status_code=502, detail="Couldn't send verification email. Try again.")

    logger.info("signup OTP sent to %s (ticket=%s)", email, ticket_id[:12])
    return TicketResponse(ticket=ticket_id, email=email, expires_in=OTP_TTL_SECONDS)


@router.post("/signup/verify", response_model=TokenResponse, summary="Verify the OTP and create the account")
async def signup_verify(body: VerifyRequest, response: Response) -> TokenResponse:
    _guard_verify()

    otp_input = body.otp.strip()
    if not otp_input.isdigit() or len(otp_input) != OTP_LENGTH:
        raise HTTPException(status_code=400, detail=f"Enter the {OTP_LENGTH}-digit code we sent.")

    ticket = _load_ticket(body.ticket)
    if ticket is None:
        raise HTTPException(status_code=410, detail="That code expired or was already used. Start again.")

    email = ticket.get("email") or ""
    if not email:
        _burn_ticket(body.ticket)
        raise HTTPException(status_code=410, detail="That signup ticket is invalid. Start again.")

    _enforce_rate_limit(
        response,
        scope="otp_verify",
        subject=email,
        limit=10,
        message="Too many verification attempts. Try again in a minute.",
    )

    attempts = int(ticket.get("attempts", 0))
    if attempts >= OTP_MAX_ATTEMPTS:
        _burn_ticket(body.ticket)
        raise HTTPException(status_code=410, detail="Too many wrong attempts. Start again.")

    if not verify_password(otp_input, ticket.get("otp_hash")):
        attempts += 1
        ticket["attempts"] = attempts
        # Best-effort re-park; if Upstash write fails we still surface the
        # 401 below so the user knows the code was wrong.
        _park_ticket(body.ticket, ticket, OTP_TTL_SECONDS)
        if attempts >= OTP_MAX_ATTEMPTS:
            _burn_ticket(body.ticket)
            raise HTTPException(status_code=410, detail="Too many wrong attempts. Start again.")
        remaining = OTP_MAX_ATTEMPTS - attempts
        raise HTTPException(
            status_code=401,
            detail=f"Wrong code. {remaining} attempt{'s' if remaining != 1 else ''} left.",
        )

    # OTP correct. Race-check: did someone else create this user while we
    # were waiting on the OTP? (e.g. concurrent GitHub OAuth signup with
    # the same email.)
    existing = await get_user_by_email(email)
    if existing is not None:
        _burn_ticket(body.ticket)
        raise HTTPException(status_code=409, detail="An account with that email already exists.")

    display = ticket.get("display_name") or email.split("@", 1)[0]
    pw_hash = ticket.get("password_hash")
    if not pw_hash:
        _burn_ticket(body.ticket)
        raise HTTPException(status_code=410, detail="That signup ticket is invalid. Start again.")

    async with get_session() as s:
        user = User(email=email, display_name=display, password_hash=pw_hash)
        s.add(user)
        try:
            await s.commit()
        except Exception as exc:
            await s.rollback()
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                _burn_ticket(body.ticket)
                raise HTTPException(status_code=409, detail="An account with that email already exists.")
            logger.exception("signup verify failed at user insert for %s", email)
            raise HTTPException(status_code=500, detail="Signup failed.")
        await s.refresh(user)

    _burn_ticket(body.ticket)

    try:
        record_audit(
            user.id, "auth.signup.otp_verified",
            target_type="user", target_id=str(user.id),
            metadata={"email": email},
        )
    except Exception:
        pass

    token = create_access_token(user)
    return TokenResponse(token=token, user=_user_payload(user))


@router.post("/signup/resend", response_model=TicketResponse, summary="Re-send a fresh OTP for an existing ticket")
async def signup_resend(body: ResendRequest, response: Response) -> TicketResponse:
    _guard_verify()
    if not USE_RESEND:
        raise HTTPException(status_code=503, detail="Email service is not configured.")

    ticket = _load_ticket(body.ticket)
    if ticket is None:
        raise HTTPException(status_code=410, detail="That signup ticket expired. Start again.")

    email = ticket.get("email") or ""
    if not email:
        _burn_ticket(body.ticket)
        raise HTTPException(status_code=410, detail="That signup ticket is invalid. Start again.")

    _enforce_rate_limit(
        response,
        scope="otp_resend",
        subject=email,
        limit=3,
        message="Too many resends. Try again in a minute.",
    )

    resends = int(ticket.get("resends", 0))
    if resends >= OTP_MAX_RESENDS:
        _burn_ticket(body.ticket)
        raise HTTPException(status_code=410, detail="Too many resend attempts. Start again.")

    new_otp = _generate_otp()
    new_payload = {
        "email": email,
        "display_name": ticket.get("display_name") or email.split("@", 1)[0],
        "password_hash": ticket.get("password_hash"),
        "otp_hash": hash_password(new_otp),
        "attempts": 0,
        "resends": resends + 1,
        "created_at": int(time.time()),
    }
    new_ticket_id = _make_ticket_id()
    if not _park_ticket(new_ticket_id, new_payload, OTP_TTL_SECONDS):
        raise HTTPException(status_code=503, detail="Could not resend right now. Try again.")

    sent = await send_otp_email(email, new_otp, OTP_TTL_SECONDS)
    if not sent:
        _burn_ticket(new_ticket_id)
        raise HTTPException(status_code=502, detail="Couldn't send verification email. Try again.")

    # Burn the old ticket only after the new one is fully provisioned, so a
    # failure halfway through doesn't strand the user with no ticket at all.
    _burn_ticket(body.ticket)

    logger.info("signup OTP re-sent to %s (ticket=%s)", email, new_ticket_id[:12])
    return TicketResponse(ticket=new_ticket_id, email=email, expires_in=OTP_TTL_SECONDS)
