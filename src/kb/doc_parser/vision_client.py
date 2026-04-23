"""OpenRouter vision client — one page image in, structured dict out.

Ports ``parsing-code-test/parse_kb.py::QwenVisionClient`` (lines 95-267)
with these production-ward adjustments:

- Reads credentials from ``src.config`` instead of a local .env.
- Bypasses LangChain; LangChain's vision wrapper adds indirection we
  don't need here and the existing agent code already uses the same env
  var, so operator mental model stays identical.
- Accumulated token usage is surfaced via ``tokens_in`` / ``tokens_out``
  properties so the parse-job runner can bill the caller.
- On cumulative cost-cap breach, raises :class:`CostCapExceeded` so the
  worker can fail the job fast without burning more quota.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any, Optional

import httpx

from src.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)

# Matches parse_kb.py constants 55-63. Pulled into the module so a single
# env override (``VISION_MODEL``) can swap the model at deploy time.
import os

VISION_MODEL: str = os.environ.get("VISION_MODEL", "qwen/qwen3-vl-32b-instruct")
MAX_TOKENS: int = 16384
MAX_RETRIES: int = 4
RETRY_BASE_DELAY: int = 2
REQUEST_TIMEOUT: int = 180

# OpenRouter's approximate USD rate for Qwen 3 VL 32B at time of writing.
# Used only for the in-process cost cap — actual billing reads the
# authoritative rate from the upstream invoice. Overridable via env so
# deployments running a different vision model can tune the cap without
# a code change.
_COST_PER_1K_IN: float = float(os.environ.get("VISION_COST_PER_1K_IN", "0.0008"))
_COST_PER_1K_OUT: float = float(os.environ.get("VISION_COST_PER_1K_OUT", "0.0016"))
_COST_CAP_USD: float = float(os.environ.get("VISION_COST_CAP_USD", "2.00"))


class CostCapExceeded(RuntimeError):
    """Per-job running cost exceeded ``VISION_COST_CAP_USD``."""


class VisionRequestError(RuntimeError):
    """All retries exhausted against OpenRouter."""


class VisionClient:
    """Page-image OCR against an OpenRouter chat-completions endpoint.

    Stateful: accumulates token usage across calls for the per-job cost
    cap + billing debit.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        key = api_key or OPENROUTER_API_KEY
        if not key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set — vision parsing requires an OpenRouter key."
            )
        self._api_key = key
        self._model = model or VISION_MODEL
        self._base_url = OPENROUTER_BASE_URL.rstrip("/") + "/chat/completions"
        self._session = httpx.Client(timeout=REQUEST_TIMEOUT)
        self._session.headers.update({
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://opengraph.tech",
            "X-Title": "OpenGraph KB Ingestion",
        })

        self.total_calls: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0

    # ------------------------------------------------------------------
    # Cost bookkeeping
    # ------------------------------------------------------------------

    @property
    def running_cost_usd(self) -> float:
        return (
            self.total_input_tokens * _COST_PER_1K_IN / 1000.0
            + self.total_output_tokens * _COST_PER_1K_OUT / 1000.0
        )

    def close(self) -> None:
        self._session.close()

    # ------------------------------------------------------------------
    # Public: one page image → structured dict
    # ------------------------------------------------------------------

    def ocr_page_image(self, image_bytes: bytes, page_num: int, total_pages: int) -> dict:
        """OCR one page and return a dict matching the per-page schema
        documented in :meth:`_build_prompt`.

        Parse-retry logic (3 attempts on bad JSON) mirrors parse_kb.py
        lines 128-141.
        """
        if self.running_cost_usd >= _COST_CAP_USD:
            raise CostCapExceeded(
                f"vision cost cap ${_COST_CAP_USD:.2f} exceeded "
                f"(running=${self.running_cost_usd:.2f})"
            )

        b64 = base64.b64encode(image_bytes).decode("utf-8")
        prompt = self._build_prompt(page_num, total_pages)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        last_raw = ""
        for parse_attempt in range(3):
            raw = self._call(messages)
            self.total_calls += 1
            last_raw = raw
            result = self._parse_response(raw, page_num)
            content = result.get("content", [])
            # If the parser fell through to the raw-text fallback and the
            # payload still looks like JSON/thinking-text, retry.
            if (
                len(content) == 1
                and content[0].get("type") == "paragraph"
                and (content[0].get("text", "").startswith("{")
                     or content[0].get("text", "").startswith("<think>"))
                and parse_attempt < 2
            ):
                logger.info(
                    "Page %d: retrying OCR (parse attempt %d/3)",
                    page_num, parse_attempt + 2,
                )
                continue
            return result

        return self._parse_response(last_raw, page_num)

    # ------------------------------------------------------------------
    # HTTP with bounded retry + 429 backoff
    # ------------------------------------------------------------------

    def _call(self, messages: list) -> str:
        payload = {"model": self._model, "messages": messages, "max_tokens": MAX_TOKENS}
        rate_limit_retries = 0
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._session.post(self._base_url, json=payload)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "30"))
                    rate_limit_retries += 1
                    if rate_limit_retries > 5:
                        raise VisionRequestError("rate-limit retries exhausted (5)")
                    logger.warning("OpenRouter 429; sleeping %ds", retry_after)
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                data = resp.json()
                usage = data.get("usage") or {}
                self.total_input_tokens += int(usage.get("prompt_tokens", 0))
                self.total_output_tokens += int(usage.get("completion_tokens", 0))
                return data["choices"][0]["message"]["content"]
            except httpx.HTTPStatusError as exc:
                logger.warning("OpenRouter HTTP %d (attempt %d/%d): %s",
                               exc.response.status_code, attempt + 1, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BASE_DELAY ** (attempt + 1))
                else:
                    raise VisionRequestError(str(exc)) from exc
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                logger.warning("OpenRouter conn error (attempt %d/%d): %s",
                               attempt + 1, MAX_RETRIES, exc)
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BASE_DELAY ** (attempt + 1))
                else:
                    raise VisionRequestError(str(exc)) from exc
        raise VisionRequestError("all retries exhausted")

    # ------------------------------------------------------------------
    # Response parsing (strips <think>, markdown fences, tolerates trailing text)
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str, page_num: int) -> dict:
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.DOTALL).strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

        direct = self._try_parse_json(cleaned)
        if direct is not None:
            return direct

        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            extracted = self._try_parse_json(cleaned[first_brace : last_brace + 1])
            if extracted is not None:
                return extracted

        logger.warning("Page %d: JSON parse failed, using raw text", page_num)
        return {
            "page": page_num,
            "content": [{"type": "paragraph", "text": raw.strip()}],
        }

    @staticmethod
    def _try_parse_json(text: str) -> Optional[dict]:
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(result, dict):
            return None
        if "content" in result and isinstance(result["content"], list):
            return result
        if "page" in result:
            result.setdefault("content", [])
            return result
        return None

    # ------------------------------------------------------------------
    # Prompt — verbatim from parse_kb.py lines 145-179.
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(page_num: int, total_pages: int) -> str:
        return f"""You are an expert document parser performing OCR on a PDF page image.
This is page {page_num} of {total_pages}.

Extract ALL content with COMPLETE accuracy. Return a JSON object.

RULES:
1. Extract ALL text VERBATIM — do not summarize, paraphrase, or omit anything
2. Preserve **bold text** by wrapping in double asterisks: **bold words here**
3. Detect headings by visual size/weight — distinguish heading1/2/3 from body text
4. For diagrams, charts, flowcharts, product images: describe comprehensively as image_description
5. For references/citations at page bottom: group as a reference item
6. For footnotes (*, †, ‡, superscript numbers): capture as footnote items
7. If page is blank or "intentionally left blank": return {{"page": {page_num}, "is_blank": true, "content": []}}
8. Multi-column layouts: read left column completely first, then right column
9. Presentation slides: describe all visual elements, diagrams, colored boxes, and extract all text
10. Tables rendered as graphics: reconstruct them as table objects with headers and rows

Return ONLY this JSON structure — no explanation, no markdown fences, no extra text:
{{
  "page": {page_num},
  "content": [
    {{"type": "heading1", "text": "Main title or chapter heading"}},
    {{"type": "heading2", "text": "Section heading"}},
    {{"type": "heading3", "text": "Subsection heading"}},
    {{"type": "paragraph", "text": "Body text with **bold emphasis** preserved"}},
    {{"type": "callout", "text": "Highlighted box, warning, or important note"}},
    {{"type": "list_bullet", "items": ["bullet item 1", "bullet item 2"]}},
    {{"type": "list_number", "items": ["numbered item 1", "numbered item 2"]}},
    {{"type": "table", "caption": "Table title if any", "headers": ["Col1", "Col2"], "rows": [{{"Col1": "val", "Col2": "val"}}]}},
    {{"type": "image_description", "text": "Detailed description of diagram/chart/photo including all visible text labels and data"}},
    {{"type": "reference", "items": ["[1] Full citation text", "[2] Full citation text"]}},
    {{"type": "footnote", "text": "Footnote text with marker"}}
  ]
}}"""
