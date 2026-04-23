"""LibreOffice headless conversion: non-PDF office docs → PDF bytes.

Thin wrapper over ``soffice --headless --convert-to pdf`` that writes the
input to a temp dir, invokes soffice, reads back the produced PDF, and
cleans up. Mirrors ``parsing-code-test/parse_kb.py::convert_to_pdf``
with:

- byte-in / byte-out signature (no filesystem contract for callers),
- Debian-slim path (``/usr/bin/soffice``) first in the candidate list.

Used only by the worker process — the API never calls soffice.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from src.kb.doc_parser.mime import DocKind

logger = logging.getLogger(__name__)

# Matches parse_kb.py line 83.
SOFFICE_TIMEOUT = 300

# Probed in order; the first one that is executable wins. The Debian-slim
# install path comes first so the CI/Render image resolves without any
# PATH lookup. The Homebrew / macOS candidates are retained so developers
# can run the worker locally against a system LibreOffice.
_SOFFICE_CANDIDATES: tuple[str, ...] = (
    "/usr/bin/soffice",
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/opt/homebrew/bin/soffice",
    "/usr/local/bin/soffice",
    "soffice",
    "libreoffice",
)

# File extension the converter writes as; LibreOffice defaults to the
# stem of the input file, so we always read ``<stem>.pdf`` back.
_EXT_FOR_KIND: dict[DocKind, str] = {
    "pptx": ".pptx",
    "ppt": ".ppt",
    "docx": ".docx",
    "doc": ".doc",
    "xlsx": ".xlsx",
    "xls": ".xls",
    "odp": ".odp",
    "odt": ".odt",
    "ods": ".ods",
    "rtf": ".rtf",
}


class SofficeMissingError(RuntimeError):
    """Raised when no LibreOffice binary is on PATH or in known install
    locations. Surfaces as a 500 with operator-visible diagnostic, not a
    user error — the container was mis-built."""


class ConversionError(RuntimeError):
    """Non-zero exit from soffice, timeout, or missing output PDF."""


def _find_soffice() -> Optional[str]:
    for candidate in _SOFFICE_CANDIDATES:
        if os.path.sep in candidate:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        else:
            resolved = shutil.which(candidate)
            if resolved:
                return resolved
    return None


def convert_to_pdf(data: bytes, kind: DocKind) -> bytes:
    """Convert an office-format document to PDF. Returns PDF bytes.

    Raises :class:`SofficeMissingError` if LibreOffice isn't installed;
    :class:`ConversionError` on any per-invocation failure.
    """
    ext = _EXT_FOR_KIND.get(kind)
    if ext is None:
        raise ConversionError(f"unsupported office kind for PDF conversion: {kind!r}")

    soffice = _find_soffice()
    if soffice is None:
        raise SofficeMissingError(
            "LibreOffice (soffice) not found. The worker image needs libreoffice-core "
            "+ libreoffice-{impress,writer,calc}. Install with: "
            "`apt-get install -y libreoffice-core libreoffice-impress libreoffice-writer "
            "libreoffice-calc` (or on macOS: `brew install --cask libreoffice`)."
        )

    # LibreOffice writes the output alongside the input, named
    # ``<stem>.pdf``. Use a unique per-call tempdir so concurrent parses
    # never collide on a filename.
    with tempfile.TemporaryDirectory(prefix="kb-convert-") as tmp_dir:
        tmp = Path(tmp_dir)
        src_path = tmp / f"source{ext}"
        src_path.write_bytes(data)

        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nologo",
            "--nofirststartwizard",
            "--convert-to", "pdf",
            "--outdir", str(tmp),
            str(src_path),
        ]
        logger.info("soffice convert: kind=%s bytes=%d -> pdf", kind, len(data))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=SOFFICE_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ConversionError(
                f"LibreOffice conversion timed out after {SOFFICE_TIMEOUT}s"
            ) from exc

        produced = tmp / f"{src_path.stem}.pdf"
        if result.returncode != 0 or not produced.exists():
            raise ConversionError(
                f"LibreOffice conversion failed (exit {result.returncode}). "
                f"stdout={result.stdout[:500]!r} stderr={result.stderr[:500]!r}"
            )
        return produced.read_bytes()
