"""Exception types for the OpenGraph SDK.

Every network failure surfaces as a subclass of :class:`OpenGraphError`
so callers can catch one base and inspect ``.status`` for the HTTP code.
"""

from __future__ import annotations

from typing import Any, Optional


class OpenGraphError(Exception):
    """Base class for every error raised by the SDK.

    Attributes:
      status: the HTTP status code returned by the server, or 0 for
        client-side / network errors.
      detail: the human-readable message from the server's JSON body.
      payload: the full parsed JSON body, or None.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        detail: Optional[str] = None,
        payload: Optional[Any] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.detail = detail
        self.payload = payload


class AuthError(OpenGraphError):
    """401 — missing or invalid API key."""


class RateLimitError(OpenGraphError):
    """429 — per-key quota exhausted.

    Attributes:
      retry_after_seconds: value of the ``Retry-After`` header, or None.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class NotFoundError(OpenGraphError):
    """404 — workspace, node, or resource doesn't exist (or isn't yours)."""


class ValidationError(OpenGraphError):
    """400 / 422 — request payload failed server-side validation."""


class ServerError(OpenGraphError):
    """5xx — transient backend failure. Safe to retry with backoff."""
