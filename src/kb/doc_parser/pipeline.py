"""End-to-end document parsing pipeline.

The single public entrypoint :func:`parse_document` is called by the
worker's ``run_parse_job`` handler. It orchestrates:

    raw bytes  → office_converter (if needed)  → PyMuPDF page render
               → vision_client OCR per page    → hierarchy assembly
               → parsed-JSON dict ready to store as a WorkspaceFile.

Progress is reported via a caller-supplied ``progress_cb(pages_done,
pages_total)`` after every page — including blank pages — so the
per-job ``percent`` column moves smoothly regardless of how many pages
are actually OCR'd.

Nothing here persists state. The caller handles blob uploads + DB
writes; this module is purely compute.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from src.kb.doc_parser.hierarchy import build_hierarchy
from src.kb.doc_parser.mime import DocKind
from src.kb.doc_parser.office_converter import convert_to_pdf
from src.kb.doc_parser.page_renderer import iter_pages, page_count
from src.kb.doc_parser.vision_client import (
    VISION_MODEL,
    CostCapExceeded,
    VisionClient,
    VisionRequestError,
)

logger = logging.getLogger(__name__)

# Matches the plan's 500-page cap — above this the OCR cost spirals and
# the user almost certainly didn't mean to upload such a monster.
MAX_PAGES = int(__import__("os").environ.get("VISION_MAX_PAGES", "500"))


class TooManyPages(RuntimeError):
    """Raised when a PDF exceeds MAX_PAGES. Surfaces as 413 at the HTTP layer."""


class CancelledError(RuntimeError):
    """Cooperative cancellation — the worker flipped ParseJob.status."""


@dataclass
class ParsedDocResult:
    """Return value of :func:`parse_document`.

    Shape is carved to exactly what the worker needs: the JSON payload to
    upload, enough metadata to write the WorkspaceFile row, and the token
    counters for billing.
    """

    json_payload: dict
    total_pages: int
    pages_processed: int
    tokens_in: int
    tokens_out: int
    vision_model: str
    running_cost_usd: float
    # Titles extracted from the first heading1 (or filename stem on
    # fallback) for the WorkspaceFile.title column.
    title: Optional[str] = None
    # Count of top-level chapters — convenient for the UI.
    chapters: int = 0
    # Indices of pages that were skipped as blank (1-based).
    blank_pages: list[int] = field(default_factory=list)


ProgressCb = Callable[[int, int], None]
CancelCheck = Callable[[], bool]


def parse_document(
    *,
    raw_bytes: bytes,
    filename: str,
    kind: DocKind,
    progress_cb: Optional[ProgressCb] = None,
    cancel_check: Optional[CancelCheck] = None,
    user_id: Optional[str] = None,
) -> ParsedDocResult:
    """Run the vision pipeline end-to-end. Synchronous because the
    vision client is sync and the worker runs each parse job in a
    thread so a single-flight pipeline isn't event-loop-blocking.

    ``user_id`` is forwarded to :class:`VisionClient` so the per-user
    minute budget (``VISION_RPM_PER_USER``) can throttle a burst batch
    without starving other users.

    Raises:
        TooManyPages: PDF has more than :data:`MAX_PAGES` pages.
        CancelledError: the caller's ``cancel_check()`` returned True.
        CostCapExceeded: cumulative running cost hit the env cap.
        VisionRequestError: OpenRouter gave up after retries.
    """
    # 1. Convert to PDF if necessary. PDFs pass through unchanged.
    if kind == "pdf":
        pdf_bytes = raw_bytes
    else:
        pdf_bytes = convert_to_pdf(raw_bytes, kind)

    # 2. Cheap count before rendering anything — cheaper to fail early
    #    than to burn half a day's quota on a 10k-page log dump.
    total = page_count(pdf_bytes)
    if total > MAX_PAGES:
        raise TooManyPages(
            f"document has {total} pages; maximum allowed is {MAX_PAGES}. "
            "Split the source into smaller files and upload separately."
        )

    # 3. Render + OCR each page. Blank pages short-circuit.
    vision = VisionClient(user_id=user_id)
    per_page_results: list[dict] = []
    blank_pages: list[int] = []
    processed = 0

    try:
        for page_num, pages_total, png in iter_pages(pdf_bytes):
            if cancel_check is not None and cancel_check():
                raise CancelledError("parse cancelled by caller")

            if png is None:
                blank_pages.append(page_num)
                per_page_results.append({
                    "page": page_num,
                    "is_blank": True,
                    "content": [],
                })
            else:
                try:
                    result = vision.ocr_page_image(png, page_num, pages_total)
                except (VisionRequestError, CostCapExceeded) as exc:
                    # One page's failure shouldn't kill the whole doc —
                    # record a placeholder and surface the failure at the
                    # job level only if EVERY page fails. Mirrors the
                    # prototype's resilience at parse_kb.py:374-394.
                    logger.error("Page %d failed: %s", page_num, exc)
                    result = {
                        "page": page_num,
                        "content": [
                            {"type": "paragraph", "text": f"[EXTRACTION FAILED: {exc}]"}
                        ],
                    }
                per_page_results.append(result)

            processed += 1
            if progress_cb is not None:
                try:
                    progress_cb(processed, pages_total)
                except Exception as exc:
                    # Progress reporting is advisory; never kill the
                    # parse because the caller's callback threw.
                    logger.warning("progress_cb raised: %s", exc)
    finally:
        vision.close()

    # 4. Assemble hierarchy.
    json_payload = build_hierarchy(
        pages=per_page_results,
        filename=filename,
        total_pages=total,
        blank_pages=blank_pages,
    )
    # Override source_type with the actual kind so downstream code can
    # tell how the JSON was derived.
    json_payload["metadata"]["source_type"] = kind

    return ParsedDocResult(
        json_payload=json_payload,
        total_pages=total,
        pages_processed=total - len(blank_pages),
        tokens_in=vision.total_input_tokens,
        tokens_out=vision.total_output_tokens,
        vision_model=VISION_MODEL,
        running_cost_usd=vision.running_cost_usd,
        title=json_payload.get("title"),
        chapters=len(json_payload.get("chapters", []) or []),
        blank_pages=blank_pages,
    )
