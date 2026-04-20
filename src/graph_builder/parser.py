"""
Recursive JSON parser for both Knowledge and Tool KB files.

The shared document schema is:
  {
    "title": ...,
    "subtitle": ...,
    "metadata_notes": [...],
    "metadata": { ... },
    "chapters": [
      {
        "heading": ...,
        "level": 1,
        "content": [ { "type": "paragraph|table|list_bullet|list_number|callout", ... } ],
        "sections": [
          {
            "heading": ..., "level": 2,
            "content": [...],
            "subsections": [ ... ]   # same structure, recursive
          }
        ]
      }
    ]
  }

ParsedKB exposes a simple flat iteration API used by the extractor.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Generator, Iterator, Union

from src.config import USE_BLOB_STORAGE

# A KB source can be either a local Path (legacy/CLI mode) or an in-memory
# ``(filename, bytes)`` tuple carrying raw JSON fetched from Vercel Blob.
KBSourceInput = Union[Path, tuple[str, bytes]]


# ---------------------------------------------------------------------------
# Low-level data containers
# ---------------------------------------------------------------------------

@dataclass
class ContentBlock:
    block_type: str          # paragraph | table | list_bullet | list_number | callout
    text: str = ""           # for paragraph / callout
    items: list[str] = field(default_factory=list)  # for list_*
    caption: str = ""        # for table
    headers: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)

    def plain_text(self) -> str:
        """Return a plain-text representation of the block."""
        if self.block_type in ("paragraph", "callout"):
            return self.text
        if self.block_type in ("list_bullet", "list_number"):
            return "\n".join(f"- {i}" for i in self.items)
        if self.block_type == "table":
            parts = []
            if self.caption:
                parts.append(self.caption)
            for row in self.rows:
                parts.append("; ".join(f"{k}: {v}" for k, v in row.items() if v))
            return "\n".join(parts)
        return ""


@dataclass
class ParsedSection:
    heading: str
    level: int
    outline_path: str           # dotted e.g. "3.2.1"
    chapter_num: int
    content_blocks: list[ContentBlock] = field(default_factory=list)
    children: list["ParsedSection"] = field(default_factory=list)
    parent_heading: str = ""

    def all_text(self) -> str:
        """Concatenated plain text from all content blocks."""
        return "\n".join(b.plain_text() for b in self.content_blocks if b.plain_text())

    def summary(self, max_chars: int = 500) -> str:
        text = self.all_text()
        return text[:max_chars] + ("…" if len(text) > max_chars else "")

    def all_tables(self) -> list[ContentBlock]:
        return [b for b in self.content_blocks if b.block_type == "table"]

    def all_paragraphs(self) -> list[str]:
        return [b.text for b in self.content_blocks
                if b.block_type in ("paragraph", "callout") and b.text]


@dataclass
class ParsedChapter:
    heading: str
    chapter_num: int
    level: int
    content_blocks: list[ContentBlock] = field(default_factory=list)
    sections: list[ParsedSection] = field(default_factory=list)

    def all_sections_flat(self) -> list[ParsedSection]:
        """BFS-flatten all sections + subsections into a single list."""
        result: list[ParsedSection] = []
        queue = list(self.sections)
        while queue:
            s = queue.pop(0)
            result.append(s)
            queue.extend(s.children)
        return result


@dataclass
class ParsedKB:
    title: str
    subtitle: str
    kb_source: str              # "knowledge" or "tool"
    metadata: dict[str, Any]
    chapters: list[ParsedChapter] = field(default_factory=list)

    def iter_chapters(self) -> Iterator[ParsedChapter]:
        yield from self.chapters

    def iter_sections(self) -> Generator[tuple[ParsedChapter, ParsedSection], None, None]:
        for ch in self.chapters:
            for sec in ch.all_sections_flat():
                yield ch, sec

    def iter_tables(self) -> Generator[tuple[ParsedChapter, ParsedSection, ContentBlock], None, None]:
        for ch, sec in self.iter_sections():
            for block in sec.all_tables():
                yield ch, sec, block


# ---------------------------------------------------------------------------
# Parser implementation
# ---------------------------------------------------------------------------

def _slugify(text: str, max_len: int = 40) -> str:
    """Convert a heading to a URL-safe slug."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    return text[:max_len]


def _parse_content_block(raw: dict[str, Any]) -> ContentBlock:
    btype = raw.get("type", "paragraph")
    if btype in ("paragraph", "callout"):
        return ContentBlock(block_type=btype, text=raw.get("text", ""))
    if btype in ("list_bullet", "list_number"):
        return ContentBlock(block_type=btype, items=raw.get("items", []))
    if btype == "table":
        return ContentBlock(
            block_type="table",
            caption=raw.get("caption", ""),
            headers=raw.get("headers", []),
            rows=raw.get("rows", []),
        )
    # Unknown block type – treat as paragraph
    return ContentBlock(block_type="paragraph", text=str(raw))


def _parse_section_recursive(
    raw: dict[str, Any],
    chapter_num: int,
    parent_path: str,
    sibling_idx: int,
) -> ParsedSection:
    """Parse a section (or subsection) dict recursively."""
    heading = raw.get("heading", "")
    level = raw.get("level", 2)
    outline_path = f"{parent_path}.{sibling_idx + 1}" if parent_path else str(sibling_idx + 1)

    blocks = [_parse_content_block(b) for b in raw.get("content", [])]

    children: list[ParsedSection] = []
    for idx, sub_raw in enumerate(raw.get("subsections", [])):
        child = _parse_section_recursive(sub_raw, chapter_num, outline_path, idx)
        child.parent_heading = heading
        children.append(child)

    return ParsedSection(
        heading=heading,
        level=level,
        outline_path=outline_path,
        chapter_num=chapter_num,
        content_blocks=blocks,
        children=children,
        parent_heading="",
    )


def _extract_chapter_num(heading: str, fallback: int = 0) -> int:
    """Extract integer chapter number from headings like 'Chapter 3: ...'."""
    m = re.search(r"Chapter\s+(\d+)", heading, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return fallback


def parse_kb_file(source: KBSourceInput, kb_source: str) -> ParsedKB:
    """
    Load and parse a KB JSON source into a ParsedKB object.

    Args:
        source: Either a local Path (legacy/CLI mode) or a ``(filename, bytes)``
            tuple carrying raw JSON fetched from Vercel Blob.
        kb_source: "knowledge" or "tool" – used for node ID namespacing.

    Cloud-first: the API pipeline always passes ``(filename, bytes)`` produced
    by downloading from Vercel Blob, so no local disk read occurs at request
    time. The Path branch is retained only so the legacy CLI helpers under
    ``scripts/`` keep working against a seed kb-config folder.
    """
    if isinstance(source, tuple):
        _, data = source
        raw = json.loads(data.decode("utf-8"))
    elif isinstance(source, (str, Path)) and Path(source).is_file():
        with open(source, encoding="utf-8") as fh:
            raw = json.load(fh)
    else:
        raise ValueError(
            f"parse_kb_file: unsupported source {source!r} — pass a Path or "
            "(filename, bytes) tuple."
        )

    chapters: list[ParsedChapter] = []
    for ch_idx, ch_raw in enumerate(raw.get("chapters", [])):
        ch_heading = ch_raw.get("heading", f"Chapter {ch_idx + 1}")
        ch_num = _extract_chapter_num(ch_heading, fallback=ch_idx + 1)
        ch_blocks = [_parse_content_block(b) for b in ch_raw.get("content", [])]

        sections: list[ParsedSection] = []
        for sec_idx, sec_raw in enumerate(ch_raw.get("sections", [])):
            sec = _parse_section_recursive(sec_raw, ch_num, str(ch_num), sec_idx)
            sec.parent_heading = ch_heading
            sections.append(sec)

        chapters.append(ParsedChapter(
            heading=ch_heading,
            chapter_num=ch_num,
            level=ch_raw.get("level", 1),
            content_blocks=ch_blocks,
            sections=sections,
        ))

    return ParsedKB(
        title=raw.get("title", ""),
        subtitle=raw.get("subtitle", ""),
        kb_source=kb_source,
        metadata=raw.get("metadata", {}),
        chapters=chapters,
    )


def _source_name(source: KBSourceInput) -> str:
    if isinstance(source, tuple):
        return Path(source[0]).stem
    return Path(source).stem


def parse_kb_files(sources: list[KBSourceInput], kb_source: str) -> ParsedKB:
    """
    Parse multiple KB JSON sources and merge them into a single ParsedKB.

    - Chapters are concatenated across files.
    - chapter_num is remapped to be globally unique across the merged set:
      file 0's chapters stay at their original numbers; file 1's chapters
      get offset by (max of file 0) + 1, etc. The heading text is prefixed
      with "[<filename>] " so the origin remains debuggable in the UI.
    - The output ParsedKB.title is "<first file title> (+N more)".
    - Empty list returns a ParsedKB with no chapters (callers should guard
      against this upstream rather than relying on silent no-op).

    Args:
        sources: list of Paths OR ``(filename, bytes)`` tuples. Duplicates are
            allowed; callers are responsible for dedup.
        kb_source: "knowledge" or "tool" — applied uniformly to every chapter.
    """
    if not sources:
        return ParsedKB(title="", subtitle="", kb_source=kb_source, metadata={}, chapters=[])

    parsed_each: list[ParsedKB] = [parse_kb_file(s, kb_source) for s in sources]

    merged: list[ParsedChapter] = []
    offset = 0
    for file_idx, (source, pkb) in enumerate(zip(sources, parsed_each)):
        # Largest chapter_num in this file determines the offset for the next one.
        local_max = 0
        fname = _source_name(source)
        for ch in pkb.chapters:
            new_num = ch.chapter_num + offset
            prefixed_heading = ch.heading if file_idx == 0 else f"[{fname}] {ch.heading}"
            merged.append(ParsedChapter(
                heading=prefixed_heading,
                chapter_num=new_num,
                level=ch.level,
                content_blocks=ch.content_blocks,
                sections=ch.sections,
            ))
            if ch.chapter_num > local_max:
                local_max = ch.chapter_num
        offset += max(local_max, len(pkb.chapters))

    first = parsed_each[0]
    title = first.title
    if len(parsed_each) > 1:
        title = f"{title} (+{len(parsed_each) - 1} more)"

    return ParsedKB(
        title=title,
        subtitle=first.subtitle,
        kb_source=kb_source,
        metadata={
            **first.metadata,
            "merged_files": [_source_name(s) for s in sources],
            "merged_count": len(sources),
        },
        chapters=merged,
    )
