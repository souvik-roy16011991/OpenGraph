"""Per-page OCR results → chapter/section/subsection JSON.

Ports ``parsing-code-test/parse_kb.py::PdfParser._build_hierarchy`` (lines
402-562) with zero behavioural drift. The output shape matches what
``src.graph_builder.parser`` already consumes, which is the whole point:
the downstream graph build doesn't know or care whether the JSON came
from a hand-authored upload or from the vision pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.kb.doc_parser.vision_client import VISION_MODEL

PARSER_VERSION = "2.0"


def build_hierarchy(
    pages: list[dict],
    filename: str,
    total_pages: int,
    blank_pages: list[int],
) -> dict[str, Any]:
    """Assemble per-page OCR dicts into a full KB document."""
    now = datetime.now(timezone.utc).isoformat()

    chapters: list[dict] = []
    current_chapter: dict | None = None
    current_section: dict | None = None
    current_subsection: dict | None = None
    table_count = 0
    total_content_items = 0

    title = filename.rsplit(".", 1)[0]
    subtitle = ""

    def _target() -> list | None:
        if current_subsection is not None:
            return current_subsection["content"]
        if current_section is not None:
            return current_section["content"]
        if current_chapter is not None:
            return current_chapter["content"]
        return None

    def _ensure_chapter(page_num: int) -> None:
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

        content_items = page_data.get("content", []) or []
        page_num = page_data.get("page", 0)

        for item in content_items:
            item_type = item.get("type", "paragraph")
            total_content_items += 1

            if item_type == "heading1":
                text = (item.get("text") or "").strip()
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
                text = (item.get("text") or "").strip()
                if not text:
                    continue
                _ensure_chapter(page_num)
                current_section = {
                    "heading": text,
                    "level": 2,
                    "content": [],
                    "subsections": [],
                }
                assert current_chapter is not None  # set by _ensure_chapter
                current_chapter["sections"].append(current_section)
                current_subsection = None

            elif item_type == "heading3":
                text = (item.get("text") or "").strip()
                if not text:
                    continue
                _ensure_chapter(page_num)
                current_subsection = {
                    "heading": text,
                    "level": 3,
                    "content": [],
                }
                if current_section is not None:
                    current_section["subsections"].append(current_subsection)
                else:
                    assert current_chapter is not None
                    current_chapter.setdefault("subsections", []).append(current_subsection)

            elif item_type == "table":
                _ensure_chapter(page_num)
                target = _target()
                if target is None:
                    continue
                table_count += 1
                headers = item.get("headers") or []
                rows = item.get("rows") or []
                row_objects: list[dict] = []
                for row in rows:
                    if isinstance(row, dict):
                        row_objects.append(row)
                    elif isinstance(row, list):
                        n = len(headers)
                        padded = list(row) + [""] * max(0, n - len(row))
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
                # image_description, reference, footnote — all pass through.
                _ensure_chapter(page_num)
                target = _target()
                if target is not None:
                    target.append(item)

    # Fallback: pages had content but no heading1 ever appeared — wrap each
    # page as a degenerate chapter so the graph builder still sees chapters.
    if not chapters:
        for page_data in pages:
            if page_data.get("is_blank"):
                continue
            chapters.append({
                "heading": f"Page {page_data.get('page', 0)}",
                "level": 1,
                "content": page_data.get("content", []) or [],
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
            "vision_model": VISION_MODEL,
        },
        "chapters": chapters,
    }
