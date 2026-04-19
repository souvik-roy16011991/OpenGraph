"""
Build a ``DomainProfile`` for any kb-config folder — works for any topic.

Inputs used, in decreasing order of trust:
    1. ``domain.yaml`` fields (manifest_overrides)
    2. ``.generated/profile.yaml`` cached LLM-polished output
    3. Optional one-shot LLM polish over KB-scan signals
    4. Deterministic KB-scan (chapter headings + tool column values)
    5. User ``--domain`` hint or folder name, with generic templates

The LLM polish step is optional and gated behind ``use_llm`` + the presence of
``OPENROUTER_API_KEY``. The result is cached to ``.generated/profile.yaml`` so
repeat runs are fully offline and deterministic.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from src.kb_config import DEFAULT_TOOL_COLUMN_KEYWORDS, DomainProfile, write_profile_cache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def profile_domain(
    *,
    kb_root: Path,
    knowledge_kb_path: Path,
    tool_kb_path: Path,
    domain_hint: Optional[str],
    manifest_overrides: dict[str, Any],
    cached_profile: dict[str, Any],
    use_llm: bool,
) -> DomainProfile:
    """Return a fully-populated ``DomainProfile`` for *kb_root*.

    The caller (src/kb_config.py) has already read ``domain.yaml`` and the
    cached ``.generated/profile.yaml`` — we merge them with fresh KB-scan
    signals and optionally polish via the LLM.
    """
    knowledge_raw = _load_json(knowledge_kb_path)
    tool_raw = _load_json(tool_kb_path)

    chapter_headings = _top_chapter_headings(knowledge_raw)
    tool_names = _top_tool_names(tool_raw)

    scan_defaults = _build_scan_defaults(
        kb_root=kb_root,
        domain_hint=domain_hint,
        knowledge_raw=knowledge_raw,
        tool_raw=tool_raw,
        chapter_headings=chapter_headings,
        tool_names=tool_names,
    )

    # Merge order (low → high priority): scan < cache < manifest
    merged = {**scan_defaults, **_normalize(cached_profile), **_normalize(manifest_overrides)}

    # If the caller wants LLM polish and every slot still looks like a scan
    # default (no manifest/cached override, no prior polish), try the LLM.
    if use_llm and not cached_profile and _llm_would_help(merged, scan_defaults):
        polished = _llm_polish(
            domain_hint=domain_hint,
            knowledge_title=knowledge_raw.get("title", ""),
            tool_title=tool_raw.get("title", ""),
            chapter_headings=chapter_headings,
            tool_names=tool_names,
            scan_defaults=scan_defaults,
        )
        if polished:
            # Cache and re-apply manifest on top (manifest always wins).
            profile = _to_profile({**scan_defaults, **polished, **_normalize(manifest_overrides)})
            try:
                write_profile_cache(kb_root, profile)
            except Exception as exc:
                logger.warning(f"Failed to write profile cache: {exc}")
            return profile

    return _to_profile(merged)


# ---------------------------------------------------------------------------
# Deterministic KB scan
# ---------------------------------------------------------------------------

_SKIP_HEADING_PREFIXES = ("preamble", "introduction", "overview", "foreword", "glossary", "appendix")


def _top_chapter_headings(raw: dict[str, Any], limit: int = 8) -> list[str]:
    """Return cleaned chapter headings, skipping preambles and numbering prefixes."""
    out: list[str] = []
    for ch in raw.get("chapters", []):
        h = _clean_heading(ch.get("heading", ""))
        low = h.lower()
        if not h or any(low.startswith(p) for p in _SKIP_HEADING_PREFIXES):
            continue
        out.append(h)
        if len(out) >= limit:
            break
    return out


def _top_tool_names(raw: dict[str, Any], limit: int = 8) -> list[str]:
    """Walk every chapter/section table; collect values from tool-like columns."""
    names: list[str] = []
    seen: set[str] = set()

    def _visit_tables(node: dict[str, Any]) -> None:
        for block in node.get("content", []) or []:
            if block.get("type") != "table":
                continue
            headers = block.get("headers", [])
            col = _primary_tool_column(headers)
            if not col:
                continue
            for row in block.get("rows", []):
                val = (row.get(col) or "").strip()
                if not val:
                    continue
                # take first name only if column is comma-separated
                val = re.split(r"[,/;]", val)[0].strip()
                key = val.lower()
                if key not in seen and 2 <= len(val) <= 60:
                    seen.add(key)
                    names.append(val)
                    if len(names) >= limit:
                        return
        for sec in node.get("sections", []) or []:
            _visit_tables(sec)
            for sub in sec.get("subsections", []) or []:
                _visit_tables(sub)

    for ch in raw.get("chapters", []):
        _visit_tables(ch)
        if len(names) >= limit:
            break
    return names


def _primary_tool_column(headers: Iterable[str]) -> Optional[str]:
    for h in headers:
        if any(k.lower() in h.lower() for k in DEFAULT_TOOL_COLUMN_KEYWORDS):
            return h
    return None


def _clean_heading(heading: str) -> str:
    """Strip 'Chapter N:' / 'Section N:' / numeric prefixes and excess whitespace."""
    heading = re.sub(
        r"^\s*(?:chapter|section|part|module|unit)\s+\d+\s*[:.\-–]?\s*",
        "",
        heading,
        flags=re.IGNORECASE,
    )
    heading = re.sub(r"^\s*\d+(\.\d+)*\s*[:.\-–]?\s*", "", heading)
    return heading.strip(" -–:")


def _join_examples(items: list[str], max_items: int = 5, max_chars: int = 180) -> str:
    """Join *items* into a comma-separated phrase, truncated sensibly."""
    if not items:
        return ""
    picked: list[str] = []
    total = 0
    for item in items[:max_items]:
        low = item.lower()
        addition = len(low) + 2
        if total + addition > max_chars and picked:
            break
        picked.append(low)
        total += addition
    return ", ".join(picked)


# ---------------------------------------------------------------------------
# Scan defaults
# ---------------------------------------------------------------------------

def _build_scan_defaults(
    *,
    kb_root: Path,
    domain_hint: Optional[str],
    knowledge_raw: dict[str, Any],
    tool_raw: dict[str, Any],
    chapter_headings: list[str],
    tool_names: list[str],
) -> dict[str, str]:
    folder_name = kb_root.name.replace("-", " ").replace("_", " ").strip()

    # domain_name — snake_case, machine-friendly
    raw_name = (domain_hint or folder_name).strip() or "custom_domain"
    domain_name = _snake_case(raw_name)

    # domain_display_name — human-friendly
    kb_title = _clean_heading(str(knowledge_raw.get("title") or ""))
    if domain_hint:
        domain_display_name = _title_case(domain_hint)
    elif kb_title and not kb_title.lower().endswith(".docx"):
        domain_display_name = _title_case(kb_title)
    else:
        domain_display_name = _title_case(folder_name or "Custom Domain")

    # organization_name — try metadata; else generic template
    org_meta = (
        knowledge_raw.get("metadata", {}).get("organization")
        or tool_raw.get("metadata", {}).get("organization")
        or ""
    ).strip()
    organization_name = org_meta or f"your {domain_display_name} team"

    knowledge_focus_examples = (
        _join_examples(chapter_headings)
        or f"core concepts and policies in {domain_display_name.lower()}"
    )
    tool_focus_examples = (
        _join_examples(tool_names)
        or f"systems and platforms used by {organization_name}"
    )

    return {
        "domain_name": domain_name,
        "domain_display_name": domain_display_name,
        "organization_name": organization_name,
        "knowledge_focus_examples": knowledge_focus_examples,
        "tool_focus_examples": tool_focus_examples,
    }


# ---------------------------------------------------------------------------
# LLM polish (optional)
# ---------------------------------------------------------------------------

def _llm_would_help(merged: dict[str, str], scan_defaults: dict[str, str]) -> bool:
    """Return True if no manifest/cache overrides were applied — the fields
    still reflect the raw scan, so polishing them is worth a call."""
    return all(merged.get(k) == scan_defaults.get(k) for k in scan_defaults)


def _llm_polish(
    *,
    domain_hint: Optional[str],
    knowledge_title: str,
    tool_title: str,
    chapter_headings: list[str],
    tool_names: list[str],
    scan_defaults: dict[str, str],
) -> Optional[dict[str, str]]:
    """One-shot LLM call to produce a natural-language profile.

    Returns ``None`` on any failure (missing key, network error, bad JSON) so
    callers transparently fall back to the scan defaults.
    """
    try:
        from src.config import (
            LLM_MODEL,
            LLM_TEMPERATURE,
            OPENROUTER_API_KEY,
            OPENROUTER_BASE_URL,
        )
    except Exception:
        return None
    if not OPENROUTER_API_KEY:
        return None

    try:
        from langchain_openai import ChatOpenAI
    except Exception:
        return None

    prompt = (
        "You are profiling a knowledge-base folder so a downstream agent can "
        "answer questions about its domain.\n\n"
        f"User-supplied domain hint: {domain_hint or '(none)'}\n"
        f"Knowledge KB title: {knowledge_title or '(unknown)'}\n"
        f"Tool KB title: {tool_title or '(unknown)'}\n"
        f"Top knowledge chapter headings: {json.dumps(chapter_headings)}\n"
        f"Top tool/system names: {json.dumps(tool_names)}\n\n"
        "Return ONLY a JSON object with these exact string keys:\n"
        "  domain_name              — snake_case slug (letters, digits, underscores)\n"
        "  domain_display_name      — natural Title Case phrase\n"
        "  organization_name        — e.g. 'the Regulatory Affairs team'; start with 'the'\n"
        "  knowledge_focus_examples — comma-separated phrase of ~5 topics (lowercase)\n"
        "  tool_focus_examples      — comma-separated phrase of ~5 systems/platforms (lowercase)\n"
        "No markdown, no explanation."
    )

    try:
        llm = ChatOpenAI(
            model=LLM_MODEL,
            openai_api_base=OPENROUTER_BASE_URL,
            openai_api_key=OPENROUTER_API_KEY,
            temperature=LLM_TEMPERATURE,
            max_tokens=512,
        )
        result = llm.invoke(prompt)
        text = result.content if hasattr(result, "content") else str(result)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        parsed = json.loads(match.group())
    except Exception as exc:
        logger.warning(f"Domain-profile LLM polish failed: {exc}")
        return None

    expected = {
        "domain_name",
        "domain_display_name",
        "organization_name",
        "knowledge_focus_examples",
        "tool_focus_examples",
    }
    out = {k: str(v).strip() for k, v in parsed.items() if k in expected and v}
    # Enforce snake_case on the slug even if the LLM slipped.
    if "domain_name" in out:
        out["domain_name"] = _snake_case(out["domain_name"])
    # Keep only polished fields that are non-empty; fall back to scan for the rest.
    return {**scan_defaults, **out}


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"KB JSON not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"KB JSON root must be an object: {path}")
    return data


def _snake_case(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "custom_domain"


def _title_case(text: str) -> str:
    text = text.strip()
    if not text:
        return "Custom Domain"
    # Preserve already-capitalized acronyms; title-case the rest.
    parts = re.split(r"\s+", text)
    def _cap(w: str) -> str:
        if w.isupper() and len(w) > 1:
            return w
        return w[:1].upper() + w[1:]
    return " ".join(_cap(p) for p in parts)


def _normalize(d: dict[str, Any]) -> dict[str, str]:
    """Keep only string-valued profile fields (ignore unrelated manifest keys)."""
    valid = {
        "domain_name",
        "domain_display_name",
        "organization_name",
        "knowledge_focus_examples",
        "tool_focus_examples",
    }
    out: dict[str, str] = {}
    for k, v in (d or {}).items():
        if k in valid and isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


def _to_profile(merged: dict[str, str]) -> DomainProfile:
    return DomainProfile(
        domain_name=merged["domain_name"],
        domain_display_name=merged["domain_display_name"],
        organization_name=merged["organization_name"],
        knowledge_focus_examples=merged["knowledge_focus_examples"],
        tool_focus_examples=merged["tool_focus_examples"],
    )
