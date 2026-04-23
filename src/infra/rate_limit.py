"""
Fixed-window rate limiting on Upstash Redis.

Each limited request issues an ``INCR counter:<bucket>`` against Upstash.
When the counter returns 1 (i.e. the key was just created) we follow with
``EXPIRE counter:<bucket> 60`` so Upstash cleans it up at the end of the
window. Steady-state requests pay one Upstash round-trip; only the first
request of every window pays two.

Why fixed-window vs. sliding-window:

  - We need atomic counting without a Lua script (Upstash REST can't run
    MULTI/EXEC blocks cheaply).
  - INCR is naturally atomic per-key, and EXPIRE-on-1 gives us a clean
    lifecycle.
  - The "burst across window boundary" flaw (a caller can spend 60 req at
    T=59s and another 60 at T=61s) is acceptable at v1 abuse thresholds.
    If it shows up in real traffic, switch to sliding-window with a
    sorted-set (still one Upstash command via ZADD + ZCOUNT).

The limiter degrades open when Upstash is misconfigured / offline — a
broken limiter should never take the product down. Operators can tighten
this to fail-closed via env if needed.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, Response
from fastapi import status as http_status

from src.api.api_key_auth import ApiKeyContext, require_api_key
from src.infra import upstash

logger = logging.getLogger(__name__)


# Fail-closed means: if Upstash returns None (offline / misconfigured) we
# reject the request. Default is fail-open — cache outages MUST NOT cascade
# into user-facing 5xx on the hot path. Operators who care about strict
# quota enforcement during outages flip ``RATE_LIMIT_FAIL_CLOSED=1``.
_FAIL_CLOSED = os.environ.get("RATE_LIMIT_FAIL_CLOSED", "0") == "1"

# Window size for every limiter. v1 ships with a single minute-long window;
# multi-window (per-hour / per-day) can layer later without API changes.
_WINDOW_SECONDS = 60

# Bump this to invalidate all live counters on deploy (e.g. after a bad
# surge). Matches the pattern used elsewhere for cache version tags.
_VERSION = os.environ.get("RATE_LIMIT_VERSION", "v1")


@dataclass(frozen=True)
class RateLimitDecision:
    """Return value from :func:`check` — tells the caller whether the
    request should proceed and carries the quota headers for the response.
    """
    allowed: bool
    limit: int
    remaining: int
    reset_at_unix: int


def _bucket_key(scope: str, subject: str) -> str:
    """Namespace the counter so different limiters can share Upstash without
    colliding — e.g. ``rl:v1:ext_key:<uuid>:<minute>``.

    The minute is included in the key itself so an old window's counter
    naturally orphans when its TTL fires; no separate reset logic.
    """
    minute = int(time.time()) // _WINDOW_SECONDS
    return f"rl:{_VERSION}:{scope}:{subject}:{minute}"


def check(scope: str, subject: str, limit: int) -> RateLimitDecision:
    """Increment the counter for ``(scope, subject)`` and decide whether the
    request is under ``limit`` for the current minute.

    Args:
      scope:   coarse limiter name — ``"ext_key"`` for API keys today.
      subject: identity within the scope — an API key UUID string.
      limit:   requests per ``_WINDOW_SECONDS``.

    Returns a :class:`RateLimitDecision`. The caller is expected to
    (a) copy ``X-RateLimit-*`` headers onto the response, and (b) 429 if
    ``allowed`` is False.
    """
    key = _bucket_key(scope, subject)
    raw = upstash._request(["INCR", key])
    # INCR returns the new count as an int (or sometimes a numeric string
    # depending on the Upstash client version). Treat anything else as
    # "we couldn't reach Upstash".
    count: Optional[int] = None
    if isinstance(raw, int):
        count = raw
    elif isinstance(raw, str) and raw.isdigit():
        count = int(raw)

    # Second command: set the TTL when we just created the counter. We
    # don't need to do this on every hit — only the window's first request
    # needs to pin expiry. Upstash auto-extends neither; a missing EXPIRE
    # would leak the key forever, which would eventually fill the free
    # tier. Hence: always fire on count == 1.
    if count == 1:
        upstash._request(["EXPIRE", key, _WINDOW_SECONDS])

    now_unix = int(time.time())
    reset_at_unix = ((now_unix // _WINDOW_SECONDS) + 1) * _WINDOW_SECONDS

    if count is None:
        # Upstash misconfigured or down. Behaviour toggles on _FAIL_CLOSED.
        if _FAIL_CLOSED:
            return RateLimitDecision(
                allowed=False, limit=limit, remaining=0, reset_at_unix=reset_at_unix,
            )
        logger.warning("Rate limit check for %s/%s: Upstash unreachable, allowing.",
                       scope, subject)
        return RateLimitDecision(
            allowed=True, limit=limit, remaining=limit, reset_at_unix=reset_at_unix,
        )

    remaining = max(0, limit - count)
    return RateLimitDecision(
        allowed=count <= limit,
        limit=limit,
        remaining=remaining,
        reset_at_unix=reset_at_unix,
    )


def apply_headers(response: Response, decision: RateLimitDecision) -> None:
    """Copy ``X-RateLimit-*`` headers onto *response*. Follows the
    draft-ietf-httpapi-ratelimit-headers convention so SDKs can parse
    them without per-vendor logic.
    """
    response.headers["X-RateLimit-Limit"] = str(decision.limit)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
    response.headers["X-RateLimit-Reset"] = str(decision.reset_at_unix)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------

def rate_limit_per_key(
    response: Response,
    ctx: ApiKeyContext = Depends(require_api_key),
) -> ApiKeyContext:
    """Dependency that enforces the API key's per-minute quota.

    Counter bucket is keyed on ``ext_key:<uuid>:<minute>`` so every key
    has its own budget, and the minute component rolls the window
    automatically via the key name (no separate reset routine).
    """
    decision = check(
        scope="ext_key",
        subject=str(ctx.key_id),
        limit=ctx.rate_limit_rpm,
    )
    apply_headers(response, decision)

    if not decision.allowed:
        response.headers["Retry-After"] = str(
            max(1, decision.reset_at_unix - int(time.time()))
        )
        raise HTTPException(
            status_code=http_status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded — {decision.limit} requests per minute. "
                f"Retry after {decision.reset_at_unix - int(time.time())} seconds."
            ),
            headers={
                "X-RateLimit-Limit": str(decision.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(decision.reset_at_unix),
                "Retry-After": str(max(1, decision.reset_at_unix - int(time.time()))),
            },
        )

    return ctx
