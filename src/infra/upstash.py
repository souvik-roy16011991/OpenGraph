"""
Upstash Redis REST adapter.

Uses the Upstash HTTP REST API (no persistent TCP connection, no redis-py
driver) so the client is deploy-friendly on serverless / autoscaling targets
like Vercel and Render. Every helper is a no-op when
``USE_UPSTASH`` is False, which lets callers drop cache reads/writes into hot
paths without branching on a feature flag themselves.

Typical payload shape of an Upstash REST response:
    {"result": <string | null>}       # GET, DEL
    {"result": "OK"}                  # SET
Any HTTP 4xx/5xx returns a JSON body with an ``error`` key; we log and return
``None`` so cache failures never take down a request.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import httpx

from src.config import (
    UPSTASH_REDIS_REST_TOKEN,
    UPSTASH_REDIS_REST_URL,
    USE_UPSTASH,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10.0


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {UPSTASH_REDIS_REST_TOKEN}",
        "Content-Type": "application/json",
    }


def _request(command: list[Any]) -> Optional[Any]:
    """POST a single Redis command (as a JSON array) to the Upstash REST endpoint.

    Returns the ``result`` field, or None on any error / when Upstash is not
    configured. The caller should always treat cache as best-effort.
    """
    if not USE_UPSTASH:
        return None
    try:
        resp = httpx.post(
            UPSTASH_REDIS_REST_URL,
            headers=_auth_headers(),
            json=command,
            timeout=_DEFAULT_TIMEOUT,
        )
        if resp.status_code >= 400:
            logger.warning("Upstash %s failed: %s %s", command[0], resp.status_code, resp.text[:200])
            return None
        body = resp.json()
        if "error" in body:
            logger.warning("Upstash %s error: %s", command[0], body["error"])
            return None
        return body.get("result")
    except Exception as exc:
        logger.warning("Upstash %s exception: %s", command[0], exc)
        return None


# ---------------------------------------------------------------------------
# Core key/value helpers
# ---------------------------------------------------------------------------

def get(key: str) -> Optional[str]:
    """Return the raw string value for *key*, or None if missing / Upstash off."""
    result = _request(["GET", key])
    return result if isinstance(result, str) else None


def set(key: str, value: str, ttl_seconds: Optional[int] = None) -> bool:
    """Set *key* to *value* with optional TTL. Returns True on success."""
    cmd: list[Any] = ["SET", key, value]
    if ttl_seconds is not None and ttl_seconds > 0:
        cmd.extend(["EX", ttl_seconds])
    return _request(cmd) == "OK"


def delete(*keys: str) -> int:
    """DEL one or more keys. Returns the number of keys removed."""
    if not keys:
        return 0
    result = _request(["DEL", *keys])
    if isinstance(result, int):
        return result
    if isinstance(result, str) and result.isdigit():
        return int(result)
    return 0


# ---------------------------------------------------------------------------
# JSON helpers — thin wrappers that serialise / parse for the caller
# ---------------------------------------------------------------------------

def get_json(key: str) -> Optional[Any]:
    """GET + json.loads. Returns None if missing, unreadable, or Upstash off."""
    raw = get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Upstash key %r holds non-JSON payload; ignoring cache.", key)
        return None


def set_json(key: str, value: Any, ttl_seconds: Optional[int] = None) -> bool:
    """json.dumps + SET. Returns True on success."""
    try:
        payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        logger.warning("Upstash set_json serialization failed for %r: %s", key, exc)
        return False
    return set(key, payload, ttl_seconds=ttl_seconds)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def ping() -> bool:
    """Return True if Upstash answers PING. False when not configured or down."""
    return _request(["PING"]) == "PONG"
