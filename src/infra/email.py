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


_OTP_HTML_TEMPLATE = """\
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html dir="ltr" lang="en">
  <head>
    <meta content="width=device-width" name="viewport" />
    <link
      rel="preload"
      as="image"
      href="https://resend-attachments.s3.amazonaws.com/45b34bdb-6389-4187-831b-6b8252f779f5" />
    <meta content="text/html; charset=UTF-8" http-equiv="Content-Type" />
    <meta name="x-apple-disable-message-reformatting" />
    <meta content="IE=edge" http-equiv="X-UA-Compatible" />
    <meta name="x-apple-disable-message-reformatting" />
    <meta
      content="telephone=no,address=no,email=no,date=no,url=no"
      name="format-detection" />
  </head>
  <body style="background-color:#ffffff">
    <!--$--><!--html--><!--head-->
    <div
      style="display:none;overflow:hidden;line-height:1px;opacity:0;max-height:0;max-width:0"
      data-skip-in-text="true">
      Your OpenGraph verification code is __OTP__.
      <div>
         ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏﻿ ‌​‍‎‏
      </div>
    </div>
    <!--body-->
    <table
      border="0"
      width="100%"
      cellpadding="0"
      cellspacing="0"
      role="presentation"
      align="center">
      <tbody>
        <tr>
          <td
            style="font-family:-apple-system, BlinkMacSystemFont, &#x27;Segoe UI&#x27;, &#x27;Roboto&#x27;, &#x27;Oxygen&#x27;, &#x27;Ubuntu&#x27;, &#x27;Cantarell&#x27;, &#x27;Fira Sans&#x27;, &#x27;Droid Sans&#x27;, &#x27;Helvetica Neue&#x27;, sans-serif;font-size:1em;min-height:100%;line-height:155%;background-color:#ffffff">
            <table
              align="left"
              width="100%"
              border="0"
              cellpadding="0"
              cellspacing="0"
              role="presentation"
              style="max-width:600px;align:left;width:100%;color:#000000;background-color:#ffffff;padding-top:0px;padding-right:0px;padding-bottom:0px;padding-left:0px;border-radius:0px;border-color:#000000;line-height:155%">
              <tbody>
                <tr style="width:100%">
                  <td>
                    <p
                      style="margin:0;padding:0;font-size:1em;padding-top:0.5em;padding-bottom:0.5em">
                      Hey there,
                    </p>
                    <p
                      style="margin:0;padding:0;font-size:1em;padding-top:0.5em;padding-bottom:0.5em">
                      Welcome to <strong>OpenGraph</strong> \U0001F680
                    </p>
                    <img
                      alt="A person sits at a desk with two computer monitors displaying code, looking out a window at a fantastical, glowing city in the clouds under a starry night"
                      height="365"
                      src="https://resend-attachments.s3.amazonaws.com/45b34bdb-6389-4187-831b-6b8252f779f5"
                      style="display:block;outline:none;border:none;text-decoration:none;max-width:100%;border-radius:8px"
                      width="548" />
                    <p
                      style="margin:0;padding:0;font-size:1em;padding-top:0.5em;padding-bottom:0.5em">
                      Your one-time password (OTP) is:
                    </p>
                    <p
                      style="margin:0;padding:0;font-size:1em;padding-top:0.5em;padding-bottom:0.5em">
                      <strong>\U0001F510 __OTP__</strong>
                    </p>
                    <p
                      style="margin:0;padding:0;font-size:1em;padding-top:0.5em;padding-bottom:0.5em">
                      This code expires in __TTL__ minutes.
                    </p>
                    <p
                      style="margin:0;padding:0;font-size:1em;padding-top:0.5em;padding-bottom:0.5em">
                      If you didn’t request this, you can safely ignore this
                      email.
                    </p>
                    <p
                      style="margin:0;padding:0;font-size:1em;padding-top:0.5em;padding-bottom:0.5em">
                      —<br /><strong>Souvik Roy</strong><br />Co-Founder,
                      <a
                        href="https://www.linkedin.com/company/opengraph-tech"
                        rel="noopener noreferrer nofollow"
                        style="color:#0670DB;text-decoration-line:none;text-decoration:underline"
                        target="_blank"
                        >OpenGraph</a
                      ><br /><a
                        href="https://www.linkedin.com/in/svkry/"
                        rel="noopener noreferrer nofollow"
                        style="color:#0670DB;text-decoration-line:none;text-decoration:underline"
                        target="_blank"
                        >https://www.linkedin.com/in/svkry/</a
                      >
                    </p>
                  </td>
                </tr>
              </tbody>
            </table>
          </td>
        </tr>
      </tbody>
    </table>
    <!--/$-->
  </body>
</html>"""


def _otp_html(otp: str, ttl_minutes: int) -> str:
    """Render the OTP email body — Souvik-branded template.

    Uses str.replace with __OTP__ / __TTL__ placeholders rather than ``.format``
    or f-strings, because the template has many literal ``{`` / ``}`` and
    ``%`` characters in inline CSS that would break those formatters.
    """
    return _OTP_HTML_TEMPLATE.replace("__OTP__", otp).replace("__TTL__", str(ttl_minutes))


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
