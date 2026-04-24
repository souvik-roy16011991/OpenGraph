"""
GitHub OAuth — "Sign in with GitHub" + auto-link on verified email.

Flow (stateless, no server-side session store):

    GET  /api/v1/auth/github/login
        1. Build a short-lived JWT `state` (nonce + return_to, 10-min exp).
        2. 302 → https://github.com/login/oauth/authorize with our client_id,
           scope=read:user+user:email, state=<jwt>.

    GET  /api/v1/auth/github/callback?code=...&state=...
        1. Verify+decode `state` JWT (CSRF defence).
        2. Exchange `code` → access_token against GitHub's token endpoint.
        3. Fetch /user (profile) and /user/emails (pick primary + verified).
        4. Link or create the User row:
              - github_id match  → log in as that user.
              - email match      → attach github_id to the existing user
                                    (password unchanged; auto-link).
              - otherwise        → create a new user with password_hash=NULL.
        5. Mint our own HS256 JWT (same shape as the email/password path).
        6. 302 → ${FRONTEND_URL}/auth/complete?token=<jwt>&return_to=...
           On any error → 302 → ${FRONTEND_URL}/sign-in?error=<code>.

Why plain httpx + JWT state (rather than Authlib):
  - Two HTTP calls total; Authlib's OAuth client mostly buys a registry
    and a session-backed state store, neither of which we want.
  - JWT state keeps the backend stateless — matches the rest of the app.
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from src.api.auth import (
    create_access_token,
    invalidate_user_cache,
    normalize_email,
)
from src.config import (
    FRONTEND_URL,
    GITHUB_CLIENT_ID,
    GITHUB_CLIENT_SECRET,
    GITHUB_OAUTH_REDIRECT_URI,
    JWT_ALGORITHM,
    OAUTH_STATE_SECRET,
    USE_GITHUB_OAUTH,
    USE_NEON,
)
from src.infra.audit import record_audit
from src.infra.db import get_session
from src.infra.db_models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth/github", tags=["auth"])

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
_GITHUB_USER_URL = "https://api.github.com/user"
_GITHUB_EMAILS_URL = "https://api.github.com/user/emails"
_STATE_EXPIRES_SECONDS = 600  # 10 minutes — caps CSRF replay window.
_OAUTH_HTTP_TIMEOUT = 10.0


# ---------------------------------------------------------------------------
# Guards + state signing
# ---------------------------------------------------------------------------

def _guard() -> None:
    if not USE_NEON:
        raise HTTPException(status_code=503, detail="DATABASE_URL (Neon) is required for auth.")
    if not USE_GITHUB_OAUTH:
        raise HTTPException(status_code=503, detail="GitHub OAuth is not configured on this deployment.")


def _safe_return_to(raw: Optional[str]) -> str:
    """Only allow in-app paths. Prevents open-redirect through ?return_to=http://evil."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return "/"


def _sign_state(return_to: str) -> str:
    """Sign a short-lived JWT carrying a nonce + return_to path."""
    import jwt
    now = datetime.now(tz=timezone.utc)
    payload = {
        "nonce": secrets.token_urlsafe(16),
        "return_to": _safe_return_to(return_to),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=_STATE_EXPIRES_SECONDS)).timestamp()),
    }
    return jwt.encode(payload, OAUTH_STATE_SECRET, algorithm=JWT_ALGORITHM)


def _verify_state(token: str) -> Optional[dict]:
    """Return the decoded state claims, or None if invalid/expired/tampered."""
    if not token or not OAUTH_STATE_SECRET:
        return None
    try:
        import jwt
        from jwt import InvalidTokenError
        return jwt.decode(
            token,
            OAUTH_STATE_SECRET,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "nonce"]},
        )
    except InvalidTokenError as exc:
        logger.info("OAuth state verification failed: %s", exc)
        return None
    except Exception as exc:
        logger.warning("OAuth state decode raised: %s", exc)
        return None


def _frontend_redirect(path: str, params: dict[str, str]) -> RedirectResponse:
    """Build an absolute redirect to the frontend with query params."""
    qs = urlencode(params)
    url = f"{FRONTEND_URL}{path}" + (f"?{qs}" if qs else "")
    return RedirectResponse(url, status_code=302)


def _error_redirect(code: str) -> RedirectResponse:
    return _frontend_redirect("/sign-in", {"error": code})


# ---------------------------------------------------------------------------
# GitHub API calls
# ---------------------------------------------------------------------------

async def _exchange_code(code: str) -> Optional[str]:
    """Exchange the OAuth authorization code for a GitHub access token."""
    try:
        async with httpx.AsyncClient(timeout=_OAUTH_HTTP_TIMEOUT) as client:
            resp = await client.post(
                _GITHUB_TOKEN_URL,
                headers={"Accept": "application/json"},
                data={
                    "client_id": GITHUB_CLIENT_ID,
                    "client_secret": GITHUB_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": GITHUB_OAUTH_REDIRECT_URI,
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("GitHub token exchange network error: %s", exc)
        return None
    if resp.status_code != 200:
        logger.info("GitHub token exchange non-200: %s %s", resp.status_code, resp.text[:200])
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    token = data.get("access_token")
    return token if isinstance(token, str) and token else None


async def _fetch_github_profile(access_token: str) -> Optional[dict]:
    """Return {id, login, name, avatar_url, email} or None on failure.

    `email` is the primary+verified email; if none is verified we return
    None so the caller can reject the login rather than silently linking
    to an unverified address.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=_OAUTH_HTTP_TIMEOUT) as client:
            profile_resp = await client.get(_GITHUB_USER_URL, headers=headers)
            if profile_resp.status_code != 200:
                logger.info("GitHub /user non-200: %s", profile_resp.status_code)
                return None
            profile = profile_resp.json()

            emails_resp = await client.get(_GITHUB_EMAILS_URL, headers=headers)
            if emails_resp.status_code != 200:
                logger.info("GitHub /user/emails non-200: %s", emails_resp.status_code)
                return None
            emails = emails_resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("GitHub profile fetch failed: %s", exc)
        return None

    verified_primary = next(
        (e for e in emails if isinstance(e, dict) and e.get("primary") and e.get("verified")),
        None,
    )
    if not verified_primary:
        return None

    gh_id = profile.get("id")
    if gh_id is None:
        return None
    return {
        "id": str(gh_id),
        "login": profile.get("login") or "",
        "name": profile.get("name") or "",
        "avatar_url": profile.get("avatar_url") or "",
        "email": str(verified_primary.get("email") or "").strip(),
    }


# ---------------------------------------------------------------------------
# Link-or-create user
# ---------------------------------------------------------------------------

async def _link_or_create_user(gh: dict) -> tuple[User, bool]:
    """Find a User for this GitHub identity, linking or creating as needed.

    Returns (user, is_new) — is_new=True means we just inserted a fresh row.
    """
    email = normalize_email(gh["email"])
    github_id = gh["id"]
    display = gh.get("name") or gh.get("login") or email.split("@", 1)[0]
    avatar = gh.get("avatar_url") or None

    async with get_session() as s:
        # 1) Existing GitHub link — straight login.
        existing = (await s.execute(
            select(User).where(User.github_id == github_id)
        )).scalar_one_or_none()
        if existing is not None:
            # Refresh cached avatar/display when the row is missing them.
            changed = False
            if not existing.avatar_url and avatar:
                existing.avatar_url = avatar
                changed = True
            if not existing.display_name and display:
                existing.display_name = display
                changed = True
            if changed:
                await s.commit()
                await s.refresh(existing)
            return existing, False

        # 2) Email match — attach GitHub id to the existing password user.
        by_email = (await s.execute(
            select(User).where(User.email == email)
        )).scalar_one_or_none()
        if by_email is not None:
            by_email.github_id = github_id
            if not by_email.avatar_url and avatar:
                by_email.avatar_url = avatar
            await s.commit()
            await s.refresh(by_email)
            return by_email, False

        # 3) Brand-new user — no password.
        user = User(
            id=uuid.uuid4(),
            email=email,
            display_name=display,
            github_id=github_id,
            avatar_url=avatar,
            password_hash=None,
        )
        s.add(user)
        try:
            await s.commit()
        except Exception as exc:
            # Unique-index race: another callback beat us to this email or
            # github_id. Roll back and re-fetch whichever row now exists.
            await s.rollback()
            if "unique" not in str(exc).lower() and "duplicate" not in str(exc).lower():
                raise
            winner = (await s.execute(
                select(User).where(
                    (User.github_id == github_id) | (User.email == email)
                )
            )).scalars().first()
            if winner is None:
                raise
            return winner, False
        await s.refresh(user)
        return user, True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/login", summary="Start the GitHub OAuth flow")
async def github_login(request: Request) -> RedirectResponse:
    _guard()
    return_to = _safe_return_to(request.query_params.get("return_to"))
    state = _sign_state(return_to)
    params = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": GITHUB_OAUTH_REDIRECT_URI,
        "scope": "read:user user:email",
        "state": state,
        "allow_signup": "true",
    }
    return RedirectResponse(f"{_GITHUB_AUTHORIZE_URL}?{urlencode(params)}", status_code=302)


@router.get("/callback", summary="GitHub OAuth callback — exchange code, mint JWT")
async def github_callback(request: Request) -> RedirectResponse:
    # We redirect all errors back to /sign-in with an error code rather than
    # surfacing JSON, because the user's browser lands here directly.
    if not USE_NEON or not USE_GITHUB_OAUTH:
        return _error_redirect("oauth_not_configured")

    # GitHub may redirect with ?error= when the user denies on the consent screen.
    gh_error = request.query_params.get("error")
    if gh_error:
        logger.info("GitHub returned error on callback: %s", gh_error)
        return _error_redirect("github_denied" if gh_error == "access_denied" else "github_error")

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return _error_redirect("oauth_missing_params")

    claims = _verify_state(state)
    if claims is None:
        return _error_redirect("oauth_state_invalid")
    return_to = _safe_return_to(claims.get("return_to"))

    access_token = await _exchange_code(code)
    if not access_token:
        return _error_redirect("github_exchange_failed")

    gh = await _fetch_github_profile(access_token)
    if gh is None:
        return _error_redirect("github_email_unverified")

    try:
        user, is_new = await _link_or_create_user(gh)
    except Exception:
        logger.exception("oauth link/create failed for github_id=%s", gh.get("id"))
        return _error_redirect("oauth_internal_error")

    invalidate_user_cache(str(user.id))
    try:
        record_audit(
            user.id,
            "auth.github.signup" if is_new else "auth.github.login",
            target_type="user",
            target_id=str(user.id),
            metadata={"github_id": gh.get("id"), "email": user.email},
        )
    except Exception:
        pass

    app_token = create_access_token(user)
    # Put the JWT in the URL fragment, not the query string. Fragments are
    # never sent to the server (so Render access logs never see the token)
    # and aren't included in the Referer header when /auth/complete makes its
    # follow-up fetch. return_to stays in the query string — it's not
    # sensitive, and keeping it server-visible helps debug redirect loops.
    qs = urlencode({"return_to": return_to})
    url = f"{FRONTEND_URL}/auth/complete?{qs}#token={app_token}"
    return RedirectResponse(url, status_code=302)
