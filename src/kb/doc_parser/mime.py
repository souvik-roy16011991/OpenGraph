"""MIME / document-kind detection.

Zero external deps — magic-byte probe on the first few bytes, fallback to
the extension. Everything we need to distinguish without pulling in
``libmagic`` or ``python-magic``.

``DocKind`` is the unified kind label used by the pipeline to choose a
parsing strategy:
  - ``json``  → existing path, no OCR needed.
  - ``pdf``   → render + OCR directly.
  - ``pptx/docx/xlsx/odp/odt/ods/rtf``  → convert to PDF via LibreOffice,
    then render + OCR.
  - ``unknown`` → reject at the HTTP layer with 415.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

DocKind = Literal[
    "json",
    "pdf",
    "pptx",
    "ppt",
    "docx",
    "doc",
    "xlsx",
    "xls",
    "odp",
    "odt",
    "ods",
    "rtf",
    "unknown",
]

# Types the ingestion path accepts for OCR. ``json`` is excluded because
# the upload route fast-paths JSON through the existing (non-OCR) code.
SUPPORTED_DOC_KINDS: tuple[DocKind, ...] = (
    "pdf",
    "pptx",
    "ppt",
    "docx",
    "doc",
    "xlsx",
    "xls",
    "odp",
    "odt",
    "ods",
    "rtf",
)

# Human-facing MIME labels per kind — what we send to Vercel Blob as the
# content-type on upload so downloads return the right type.
MIME_BY_KIND: dict[DocKind, str] = {
    "json": "application/json",
    "pdf": "application/pdf",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "ppt": "application/vnd.ms-powerpoint",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "doc": "application/msword",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xls": "application/vnd.ms-excel",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "rtf": "application/rtf",
    "unknown": "application/octet-stream",
}

_EXT_TO_KIND: dict[str, DocKind] = {
    ".json": "json",
    ".pdf": "pdf",
    ".pptx": "pptx",
    ".ppt": "ppt",
    ".docx": "docx",
    ".doc": "doc",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".odp": "odp",
    ".odt": "odt",
    ".ods": "ods",
    ".rtf": "rtf",
}


def _is_pdf(head: bytes) -> bool:
    return head.startswith(b"%PDF-")


def _is_zip(head: bytes) -> bool:
    # ZIP local-file header. Every OOXML (pptx/docx/xlsx) + every ODF
    # (odp/odt/ods) file is a ZIP container.
    return head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06")


def _is_ole(head: bytes) -> bool:
    # Legacy MS compound-document magic (doc/xls/ppt).
    return head.startswith(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1")


def _is_rtf(head: bytes) -> bool:
    return head.startswith(b"{\\rtf")


def _is_json(head: bytes, raw: bytes) -> bool:
    stripped = raw.lstrip()
    if not stripped:
        return False
    return stripped[:1] in (b"{", b"[")


def sniff(raw: bytes, filename: str | None = None) -> DocKind:
    """Classify *raw* bytes (optionally informed by *filename*) as a DocKind.

    Strategy: magic-byte checks first (authoritative), with ZIP- and
    OLE-container bytes refined by the extension since we can't tell
    pptx/docx/xlsx apart from the first 4 bytes alone.
    """
    head = raw[:16]
    ext = Path(filename or "").suffix.lower()

    if _is_pdf(head):
        return "pdf"
    if _is_rtf(head):
        return "rtf"

    if _is_zip(head):
        # Must be OOXML or ODF — the extension disambiguates. A PDF mis-
        # named .pptx would never land here (PDF magic wins above).
        kind = _EXT_TO_KIND.get(ext)
        if kind in ("pptx", "docx", "xlsx", "odp", "odt", "ods"):
            return kind
        return "unknown"

    if _is_ole(head):
        kind = _EXT_TO_KIND.get(ext)
        if kind in ("ppt", "doc", "xls"):
            return kind
        return "unknown"

    if _is_json(head, raw):
        return "json"

    # No magic match — fall back to the extension alone. Useful for
    # edge cases like truncated streams where a user uploaded something
    # that starts with whitespace/BOM.
    by_ext = _EXT_TO_KIND.get(ext)
    return by_ext if by_ext is not None else "unknown"
