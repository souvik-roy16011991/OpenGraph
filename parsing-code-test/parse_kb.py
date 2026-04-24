#!/usr/bin/env python3
"""
parse_kb.py — Pharma Product Document Parser
Converts PDF/PPTX/DOCX and similar files from kb-raw/ into structured JSON in kb-config/.
Non-PDF formats are converted to PDF via LibreOffice headless (soffice).
Uses Qwen 3 VL vision model via OpenRouter for page-by-page OCR extraction.

Usage:
    python3 parse_kb.py [--api-key <key>] [--workers N] [--dpi N] [--file PATTERN] [--dry-run]
"""

from __future__ import annotations

import os
import re
import sys
import json
import time
import base64
import shutil
import hashlib
import logging
import argparse
import tempfile
import subprocess
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
WORKSPACE = Path(__file__).parent
KB_RAW = WORKSPACE / "kb-raw"
KB_CONFIG = WORKSPACE / "kb-config"
PARSER_VERSION = "2.0"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
QWEN_MODEL = os.environ.get("QWEN_MODEL", "qwen/qwen3-vl-32b-instruct")

DEFAULT_DPI = 200
MAX_PIXELS_LONG_SIDE = 4096
MAX_TOKENS = 16384
MAX_RETRIES = 4
RETRY_BASE_DELAY = 2
REQUEST_TIMEOUT = 180

# Formats we can natively ingest (already PDF) vs. formats we convert to PDF via soffice
PDF_EXTS = {".pdf"}
CONVERTIBLE_EXTS = {
    ".pptx", ".ppt",
    ".docx", ".doc",
    ".odp", ".odt", ".ods",
    ".xlsx", ".xls",
    ".rtf",
}
SUPPORTED_EXTS = PDF_EXTS | CONVERTIBLE_EXTS

SOFFICE_CANDIDATES = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/opt/homebrew/bin/soffice",
    "/usr/local/bin/soffice",
    "soffice",
    "libreoffice",
]
SOFFICE_TIMEOUT = 300

BLANK_PAGE_PATTERNS = [
    r"^\s*$",
    r"(?i)^\s*this\s+page\s+(is\s+)?intentionally\s+left\s+blank\.?\s*$",
    r"^\s*\d+\s*$",
]


# ─────────────────────────────────────────────
# Qwen Vision Client
# ─────────────────────────────────────────────
class QwenVisionClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://kb-kg-parser",
            "X-Title": "KB-KG Parser",
        })
        self.total_calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def ocr_page_image(self, image_bytes: bytes, page_num: int, total_pages: int) -> dict:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        prompt = self._build_prompt(page_num, total_pages)

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]

        # Retry up to 2 times on JSON parse failure
        last_raw = ""
        for parse_attempt in range(3):
            raw = self._call(messages)
            self.total_calls += 1
            last_raw = raw
            result = self._parse_response(raw, page_num)
            # Check if it fell through to raw text fallback
            content = result.get("content", [])
            if len(content) == 1 and content[0].get("type") == "paragraph":
                text = content[0].get("text", "")
                if text.startswith("{") or text.startswith("<think>"):
                    if parse_attempt < 2:
                        log.info(f"  Page {page_num}: retrying OCR (parse attempt {parse_attempt + 2}/3)")
                        continue
            return result

        return self._parse_response(last_raw, page_num)

    def _build_prompt(self, page_num: int, total_pages: int) -> str:
        return f"""You are an expert pharmaceutical document parser performing OCR on a PDF page image.
This is page {page_num} of {total_pages} from a pharma product document.

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

    def _call(self, messages: list) -> str:
        payload = {
            "model": QWEN_MODEL,
            "messages": messages,
            "max_tokens": MAX_TOKENS,
        }

        rate_limit_retries = 0
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.post(
                    OPENROUTER_URL, json=payload, timeout=REQUEST_TIMEOUT
                )

                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", 30))
                    rate_limit_retries += 1
                    if rate_limit_retries > 5:
                        raise RuntimeError("Too many rate limit retries")
                    log.warning(f"Rate limited, waiting {retry_after}s ...")
                    time.sleep(retry_after)
                    continue

                resp.raise_for_status()
                data = resp.json()

                usage = data.get("usage", {})
                self.total_input_tokens += usage.get("prompt_tokens", 0)
                self.total_output_tokens += usage.get("completion_tokens", 0)

                return data["choices"][0]["message"]["content"]

            except requests.HTTPError as e:
                log.warning(f"HTTP error (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BASE_DELAY ** (attempt + 1))
                else:
                    raise
            except (requests.Timeout, requests.ConnectionError) as e:
                log.warning(f"Connection error (attempt {attempt + 1}/{MAX_RETRIES}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_BASE_DELAY ** (attempt + 1))
                else:
                    raise

        raise RuntimeError("All retries exhausted")

    def _parse_response(self, raw: str, page_num: int) -> dict:
        # Strip <think>...</think> tags from Qwen 3 reasoning (greedy to handle nested)
        cleaned = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.DOTALL).strip()
        # Strip markdown fences
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

        # Try direct parse first
        result = self._try_parse_json(cleaned, page_num)
        if result:
            return result

        # Fallback: extract JSON object by finding first { and last }
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace > first_brace:
            json_str = cleaned[first_brace : last_brace + 1]
            result = self._try_parse_json(json_str, page_num)
            if result:
                return result

        log.warning(f"Page {page_num}: JSON parse failed, using raw text")
        log.debug(f"Page {page_num} raw response (first 500 chars): {repr(raw[:500])}")
        log.debug(f"Page {page_num} cleaned (first 500 chars): {repr(cleaned[:500])}")
        return {"page": page_num, "content": [{"type": "paragraph", "text": raw.strip()}]}

    def _try_parse_json(self, text: str, page_num: int) -> Optional[dict]:
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                if "content" in result and isinstance(result["content"], list):
                    return result
                # Some responses wrap content differently
                if "page" in result:
                    result.setdefault("content", [])
                    return result
        except json.JSONDecodeError:
            pass
        return None


# ─────────────────────────────────────────────
# Page Renderer
# ─────────────────────────────────────────────
class PageRenderer:
    def __init__(self, base_dpi: int = DEFAULT_DPI):
        self.base_dpi = base_dpi

    def is_blank_page(self, page) -> bool:
        text = page.get_text("text").strip()
        # If page has images/graphics, it's not blank even without extractable text
        if not text:
            images = page.get_images(full=False)
            if images:
                return False  # Has images — send to vision model
            # Check for drawings/vector graphics via display list length
            dl = page.get_displaylist()
            if dl:
                # A truly blank page has a very small display list
                rect = page.rect
                tp = page.get_textpage()
                blocks = page.get_text("dict", flags=0).get("blocks", [])
                if blocks:
                    return False  # Has some rendered content
            return True
        for pattern in BLANK_PAGE_PATTERNS:
            if re.match(pattern, text):
                # Even if text matches blank pattern, check for images
                images = page.get_images(full=False)
                if images:
                    return False
                return True
        return False

    def render_page(self, page) -> bytes:
        import fitz

        # Calculate effective DPI — cap at MAX_PIXELS_LONG_SIDE
        rect = page.rect
        width_in = rect.width / 72
        height_in = rect.height / 72
        long_side_in = max(width_in, height_in)

        effective_dpi = self.base_dpi
        if long_side_in * self.base_dpi > MAX_PIXELS_LONG_SIDE:
            effective_dpi = int(MAX_PIXELS_LONG_SIDE / long_side_in)
            effective_dpi = max(effective_dpi, 150)  # Floor at 150 DPI

        mat = fitz.Matrix(effective_dpi / 72, effective_dpi / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        return pix.tobytes("png")


# ─────────────────────────────────────────────
# PDF Parser
# ─────────────────────────────────────────────
class PdfParser:
    def __init__(self, qwen: QwenVisionClient, renderer: PageRenderer, max_workers: int = 4):
        self.qwen = qwen
        self.renderer = renderer
        self.max_workers = max_workers

    def parse(self, path: Path) -> dict:
        import fitz

        log.info(f"Opening PDF: {path.name}")
        doc = fitz.open(str(path))
        total_pages = len(doc)

        # Pre-scan for blank pages
        blank_pages = []
        pages_to_process = []
        for i in range(total_pages):
            page = doc[i]
            if self.renderer.is_blank_page(page):
                blank_pages.append(i + 1)
            else:
                pages_to_process.append(i)

        log.info(
            f"  {total_pages} pages total, {len(blank_pages)} blank, "
            f"{len(pages_to_process)} to process"
        )

        # Render and OCR non-blank pages
        page_results = {}

        def process_page(page_idx: int) -> tuple:
            page = doc[page_idx]
            page_num = page_idx + 1
            log.info(f"  Page {page_num}/{total_pages} -> rendering + OCR ...")
            img_bytes = self.renderer.render_page(page)
            result = self.qwen.ocr_page_image(img_bytes, page_num, total_pages)
            return page_num, result

        if self.max_workers > 1 and len(pages_to_process) > 1:
            workers = min(self.max_workers, len(pages_to_process))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {
                    executor.submit(process_page, idx): idx
                    for idx in pages_to_process
                }
                for future in as_completed(futures):
                    try:
                        page_num, result = future.result()
                        page_results[page_num] = result
                    except Exception as e:
                        page_idx = futures[future]
                        page_num = page_idx + 1
                        log.error(f"  Page {page_num} FAILED: {e}")
                        page_results[page_num] = {
                            "page": page_num,
                            "content": [{"type": "paragraph", "text": f"[EXTRACTION FAILED: {e}]"}],
                        }
        else:
            for idx in pages_to_process:
                try:
                    page_num, result = process_page(idx)
                    page_results[page_num] = result
                except Exception as e:
                    page_num = idx + 1
                    log.error(f"  Page {page_num} FAILED: {e}")
                    page_results[page_num] = {
                        "page": page_num,
                        "content": [{"type": "paragraph", "text": f"[EXTRACTION FAILED: {e}]"}],
                    }

        doc.close()

        # Assemble in page order
        ordered_pages = [page_results[pn] for pn in sorted(page_results.keys())]
        return self._build_hierarchy(ordered_pages, path.name, total_pages, blank_pages)

    def _build_hierarchy(
        self, pages: list, filename: str, total_pages: int, blank_pages: list
    ) -> dict:
        now = datetime.now(timezone.utc).isoformat()

        chapters = []
        current_chapter = None
        current_section = None
        current_subsection = None
        table_count = 0
        total_content_items = 0

        title = filename.rsplit(".", 1)[0]
        subtitle = ""

        def get_target():
            if current_subsection is not None:
                return current_subsection["content"]
            if current_section is not None:
                return current_section["content"]
            if current_chapter is not None:
                return current_chapter["content"]
            return None

        def ensure_chapter(page_num: int):
            nonlocal current_chapter, current_section, current_subsection
            if current_chapter is None:
                current_chapter = {
                    "heading": f"Page {page_num}",
                    "level": 1,
                    "content": [],
                    "sections": [],
                }
                chapters.append(current_chapter)
                current_section = None
                current_subsection = None

        for page_data in pages:
            if page_data.get("is_blank"):
                continue

            content_items = page_data.get("content", [])
            page_num = page_data.get("page", 0)

            for item in content_items:
                item_type = item.get("type", "paragraph")
                total_content_items += 1

                if item_type == "heading1":
                    text = item.get("text", "").strip()
                    if not text:
                        continue
                    if not chapters:
                        title = text
                    current_chapter = {
                        "heading": text,
                        "level": 1,
                        "content": [],
                        "sections": [],
                    }
                    chapters.append(current_chapter)
                    current_section = None
                    current_subsection = None

                elif item_type == "heading2":
                    text = item.get("text", "").strip()
                    if not text:
                        continue
                    ensure_chapter(page_num)
                    current_section = {
                        "heading": text,
                        "level": 2,
                        "content": [],
                        "subsections": [],
                    }
                    current_chapter["sections"].append(current_section)
                    current_subsection = None

                elif item_type == "heading3":
                    text = item.get("text", "").strip()
                    if not text:
                        continue
                    ensure_chapter(page_num)
                    current_subsection = {
                        "heading": text,
                        "level": 3,
                        "content": [],
                    }
                    if current_section is not None:
                        current_section["subsections"].append(current_subsection)
                    else:
                        if "subsections" not in current_chapter:
                            current_chapter["subsections"] = []
                        current_chapter["subsections"].append(current_subsection)

                elif item_type == "table":
                    ensure_chapter(page_num)
                    target = get_target()
                    table_count += 1

                    headers = item.get("headers", [])
                    rows = item.get("rows", [])
                    # Normalize rows to dicts if they came as lists
                    row_objects = []
                    for row in rows:
                        if isinstance(row, dict):
                            row_objects.append(row)
                        elif isinstance(row, list):
                            n = len(headers)
                            padded = row + [""] * max(0, n - len(row))
                            row_objects.append(
                                {headers[i]: padded[i] for i in range(n) if i < len(headers)}
                            )

                    target.append({
                        "type": "table",
                        "caption": item.get("caption", ""),
                        "headers": headers,
                        "rows": row_objects,
                    })

                else:
                    # paragraph, callout, list_bullet, list_number,
                    # image_description, reference, footnote
                    ensure_chapter(page_num)
                    target = get_target()
                    target.append(item)

        # Fallback: if no chapters created, wrap per page
        if not chapters:
            for page_data in pages:
                if page_data.get("is_blank"):
                    continue
                page_num = page_data.get("page", 0)
                chapters.append({
                    "heading": f"Page {page_num}",
                    "level": 1,
                    "content": page_data.get("content", []),
                    "sections": [],
                })

        return {
            "title": title,
            "subtitle": subtitle,
            "metadata_notes": [],
            "metadata": {
                "source_file": filename,
                "source_type": "pdf",
                "total_pages": total_pages,
                "pages_processed": total_pages - len(blank_pages),
                "blank_pages_skipped": blank_pages,
                "total_chapters": len(chapters),
                "total_tables": table_count,
                "total_content_items": total_content_items,
                "parsed_at": now,
                "parser_version": PARSER_VERSION,
                "vision_model_used": True,
                "vision_model": QWEN_MODEL,
            },
            "chapters": chapters,
        }


# ─────────────────────────────────────────────
# Format Conversion (PPTX/DOCX/etc. → PDF via LibreOffice)
# ─────────────────────────────────────────────
def find_soffice() -> Optional[str]:
    for candidate in SOFFICE_CANDIDATES:
        if os.path.sep in candidate:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        else:
            path = shutil.which(candidate)
            if path:
                return path
    return None


def convert_to_pdf(src: Path, out_dir: Path) -> Path:
    """Convert a non-PDF document to PDF using LibreOffice headless."""
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError(
            "LibreOffice (soffice) not found. Install with: brew install --cask libreoffice"
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        soffice,
        "--headless",
        "--norestore",
        "--nologo",
        "--nofirststartwizard",
        "--convert-to", "pdf",
        "--outdir", str(out_dir),
        str(src),
    ]
    log.info(f"  Converting {src.name} -> PDF via LibreOffice ...")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=SOFFICE_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"LibreOffice conversion timed out after {SOFFICE_TIMEOUT}s") from e

    expected = out_dir / (src.stem + ".pdf")
    if result.returncode != 0 or not expected.exists():
        raise RuntimeError(
            f"LibreOffice conversion failed (exit {result.returncode}).\n"
            f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )
    return expected


# ─────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────
def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def discover_files(kb_raw: Path, file_pattern: Optional[str] = None) -> list:
    files = []
    for fpath in sorted(kb_raw.rglob("*")):
        if not fpath.is_file():
            continue
        if fpath.suffix.lower() not in SUPPORTED_EXTS:
            continue
        if file_pattern and file_pattern.lower() not in fpath.name.lower():
            continue
        files.append(fpath)
    return files


def output_path(source_file: Path, kb_raw: Path, kb_config: Path) -> Path:
    rel = source_file.relative_to(kb_raw)
    return kb_config / rel.with_suffix(".json")


def write_json(data: dict, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    size_kb = out_path.stat().st_size / 1024
    log.info(f"  Written: {out_path.name} ({size_kb:.1f} KB)")


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Parse PDF files from kb-raw/ to kb-config/ JSON")
    parser.add_argument("--api-key", default=None, help="OpenRouter API key")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent API workers per document")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help="Base DPI for page rendering")
    parser.add_argument("--file", default=None, help="Only process files matching this pattern")
    parser.add_argument("--dry-run", action="store_true", help="List files without processing")
    args = parser.parse_args()

    api_key = args.api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        log.error(
            "No OpenRouter API key found. Set OPENROUTER_API_KEY in .env or pass --api-key."
        )
        sys.exit(2)

    # Discover files
    files = discover_files(KB_RAW, args.file)
    if not files:
        log.error(f"No supported files found in {KB_RAW} (extensions: {sorted(SUPPORTED_EXTS)})")
        sys.exit(1)

    log.info(f"Found {len(files)} file(s):")
    for f in files:
        log.info(f"  {f.relative_to(WORKSPACE)} ({f.stat().st_size / 1024:.0f} KB) [{f.suffix.lower()}]")

    # Warn up front if we need LibreOffice but don't have it
    needs_conversion = any(f.suffix.lower() in CONVERTIBLE_EXTS for f in files)
    if needs_conversion and not find_soffice():
        log.error(
            "Found non-PDF files that require LibreOffice for conversion, but soffice is not "
            "available. Install with: brew install --cask libreoffice"
        )
        sys.exit(3)

    # Detect duplicates via MD5
    hash_map: dict[str, Path] = {}
    duplicates: dict[Path, Path] = {}
    for f in files:
        h = md5_file(f)
        if h in hash_map:
            duplicates[f] = hash_map[h]
            log.info(f"  DUPLICATE: {f.name} == {hash_map[h].name}")
        else:
            hash_map[h] = f

    if args.dry_run:
        log.info("Dry run — exiting.")
        return 0

    # Initialize components
    qwen = QwenVisionClient(api_key)
    renderer = PageRenderer(base_dpi=args.dpi)
    pdf_parser = PdfParser(qwen, renderer, max_workers=args.workers)

    results = {"success": [], "failed": [], "duplicates": []}
    start_time = time.time()

    # Scratch dir for PPTX→PDF conversions
    convert_root = Path(tempfile.mkdtemp(prefix="kb-convert-"))
    log.info(f"Using conversion scratch dir: {convert_root}")

    try:
        for idx, fpath in enumerate(files, 1):
            out = output_path(fpath, KB_RAW, KB_CONFIG)
            log.info(f"\n{'=' * 60}")
            log.info(f"[{idx}/{len(files)}] {fpath.name}")

            # Handle duplicates: copy from already-processed original
            if fpath in duplicates:
                original = duplicates[fpath]
                original_out = output_path(original, KB_RAW, KB_CONFIG)
                if original_out.exists():
                    log.info(f"  Copying from duplicate: {original_out.name}")
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original_out, out)
                    results["duplicates"].append(str(fpath.relative_to(WORKSPACE)))
                    continue
                else:
                    log.info(f"  Original not yet processed, processing this copy")

            try:
                file_start = time.time()

                ext = fpath.suffix.lower()
                if ext in PDF_EXTS:
                    pdf_path = fpath
                    source_type = "pdf"
                else:
                    conv_dir = convert_root / fpath.stem
                    pdf_path = convert_to_pdf(fpath, conv_dir)
                    source_type = ext.lstrip(".")

                data = pdf_parser.parse(pdf_path)

                # Override metadata to reference the ORIGINAL source, not the converted PDF
                data["metadata"]["source_file"] = fpath.name
                data["metadata"]["source_type"] = source_type
                if source_type != "pdf":
                    data["metadata"]["intermediate_pdf"] = pdf_path.name

                write_json(data, out)
                elapsed = time.time() - file_start

                meta = data["metadata"]
                log.info(
                    f"  Done in {elapsed:.1f}s — "
                    f"{meta['total_pages']} pages, "
                    f"{meta['pages_processed']} processed, "
                    f"{meta['total_content_items']} items, "
                    f"{meta['total_tables']} tables"
                )
                results["success"].append(str(fpath.relative_to(WORKSPACE)))

            except Exception as e:
                log.error(f"  FAILED: {e}")
                traceback.print_exc()
                results["failed"].append({
                    "file": str(fpath.relative_to(WORKSPACE)),
                    "error": str(e),
                })
    finally:
        shutil.rmtree(convert_root, ignore_errors=True)

    # Final summary
    total_time = time.time() - start_time
    log.info(f"\n{'=' * 60}")
    log.info(f"COMPLETE in {total_time:.1f}s")
    log.info(f"  Succeeded: {len(results['success'])}")
    log.info(f"  Duplicates copied: {len(results['duplicates'])}")
    log.info(f"  Failed: {len(results['failed'])}")
    log.info(f"  API calls: {qwen.total_calls}")
    log.info(f"  Tokens: {qwen.total_input_tokens:,} input, {qwen.total_output_tokens:,} output")

    if results["failed"]:
        log.error("Failed files:")
        for f in results["failed"]:
            log.error(f"  {f['file']}: {f['error']}")

    return 0 if not results["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
