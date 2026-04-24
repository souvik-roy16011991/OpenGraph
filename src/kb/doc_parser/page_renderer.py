"""PDF page rendering + blank detection.

Ports ``parsing-code-test/parse_kb.py::PageRenderer`` (lines 273-319) and
``is_blank_page`` (277-301) to a reusable module. PyMuPDF (``fitz``) is
the only dependency — the wheels ship libmupdf so no apt install needed.

Caps rendered images at 4096 px on the long side (parse_kb.py constant).
Above that, OpenRouter rejects the image payload or slows to a crawl.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator

logger = logging.getLogger(__name__)

DEFAULT_DPI = 200
MAX_PIXELS_LONG_SIDE = 4096
MIN_DPI = 150

# Same heuristics as parse_kb.py line 85-89. Whitespace / centered page
# numbers / boilerplate "this page left blank" are treated as blank so we
# save API calls.
_BLANK_PAGE_PATTERNS = (
    re.compile(r"^\s*$"),
    re.compile(r"^\s*this\s+page\s+(is\s+)?intentionally\s+left\s+blank\.?\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*$"),
)


def is_blank_page(page) -> bool:
    """A page is blank when it has no meaningful text AND no images."""
    # Late import so the module loads cheaply even when PyMuPDF isn't
    # used in the current process (e.g. the API container imports
    # doc_parser but never renders).
    text = page.get_text("text").strip()
    if not text:
        images = page.get_images(full=False)
        if images:
            return False  # pure image page — must OCR
        # Even without extractable text, a page with vector graphics /
        # drawings still has rendered content worth sending.
        blocks = page.get_text("dict", flags=0).get("blocks", [])
        if blocks:
            return False
        return True

    for pat in _BLANK_PAGE_PATTERNS:
        if pat.match(text):
            images = page.get_images(full=False)
            if images:
                return False
            return True
    return False


def _effective_dpi(page, base_dpi: int) -> int:
    """Clamp the DPI so the rendered image's long side ≤ MAX_PIXELS_LONG_SIDE."""
    rect = page.rect
    width_in = rect.width / 72
    height_in = rect.height / 72
    long_side_in = max(width_in, height_in)
    if long_side_in * base_dpi <= MAX_PIXELS_LONG_SIDE:
        return base_dpi
    return max(int(MAX_PIXELS_LONG_SIDE / long_side_in), MIN_DPI)


def render_page(page, base_dpi: int = DEFAULT_DPI) -> bytes:
    """Render one PDF page as a PNG byte string, DPI-clamped."""
    import fitz  # noqa: WPS433 — late import (heavy dep)

    dpi = _effective_dpi(page, base_dpi)
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    return pix.tobytes("png")


def iter_pages(pdf_bytes: bytes, base_dpi: int = DEFAULT_DPI) -> Iterator[tuple[int, int, bytes | None]]:
    """Yield ``(page_idx_1based, total_pages, png_bytes_or_None)`` per page.

    Blank pages yield ``png_bytes = None`` so the caller can skip the OCR
    call and still emit accurate progress percentages.
    """
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        total = len(doc)
        for i in range(total):
            page = doc[i]
            if is_blank_page(page):
                yield i + 1, total, None
            else:
                yield i + 1, total, render_page(page, base_dpi)
    finally:
        doc.close()


def page_count(pdf_bytes: bytes) -> int:
    """Cheap page count without rendering. Used for server-side 500-page cap."""
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return len(doc)
    finally:
        doc.close()
