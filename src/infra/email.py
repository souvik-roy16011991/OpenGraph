"""
Transactional email — Resend HTTP API.

Single-purpose adapter used by the signup-OTP flow. We POST directly to
``https://api.resend.com/emails`` over the existing ``httpx`` client; no
SDK is needed and no persistent connection is held. Returns ``True`` on
2xx, ``False`` (with a logged exception) on anything else — callers that
need to surface a 5xx to the user check the boolean and decide.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from src.config import EMAIL_FROM, RESEND_API_KEY, USE_RESEND

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"
_DEFAULT_TIMEOUT = 10.0


def _otp_html(otp: str, ttl_minutes: int) -> str:
    """Render the OTP email body. Inline styles only — many clients strip <style>."""
    return f"""\
<!doctype html>
<html><body style="margin:0;padding:0;background:#f6f7f9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#0f172a;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="padding:32px 16px;">
    <tr><td align="center">
      <table role="presentation" width="100%" style="max-width:480px;background:#ffffff;border-radius:12px;border:1px solid #e2e8f0;padding:32px;">
        <tr><td>
          <div style="font-size:18px;font-weight:600;letter-spacing:-0.01em;margin-bottom:24px;">OpenGraph</div>
          <div style="font-size:15px;line-height:1.55;margin-bottom:20px;">Use this code to finish creating your account:</div>
          <div style="font-size:32px;font-weight:700;letter-spacing:8px;background:#f1f5f9;border-radius:8px;padding:16px 0;text-align:center;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;">{otp}</div>
          <div style="font-size:13px;color:#64748b;margin-top:20px;line-height:1.55;">This code expires in {ttl_minutes} minutes. If you didn't request it, you can safely ignore this email — no account will be created.</div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


async def send_otp_email(to: str, otp: str, ttl_seconds: int) -> bool:
    """Send a 6-digit OTP to *to*. Returns True on success."""
    if not USE_RESEND:
        logger.error("send_otp_email called but RESEND_API_KEY is not configured")
        return False

    ttl_minutes = max(1, ttl_seconds // 60)
    payload = {
        "from": EMAIL_FROM,
        "to": [to],
        "subject": f"Your OpenGraph verification code: {otp}",
        "html": _otp_html(otp, ttl_minutes),
    }
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.post(_RESEND_URL, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        logger.exception("Resend request failed for %s: %s", to, exc)
        return False

    if resp.status_code >= 400:
        # Resend returns JSON like {"name": "validation_error", "message": "..."}
        body_preview: Optional[str] = None
        try:
            body_preview = resp.text[:300]
        except Exception:
            pass
        logger.error("Resend %s for %s: %s", resp.status_code, to, body_preview)
        return False
    return True
