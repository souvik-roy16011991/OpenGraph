"""
KB template catalog — static, code-shipped, OpenRouter-style.

Each template is a YAML file under ``templates/`` at the repo root. The
catalog is parsed once at import and cached; templates are not
user-editable (contributions ship as PRs). Each template carries:

- identity metadata (slug, name, description, category, icon)
- ``domain`` — the five DomainProfile fields that ``Workspace.domain_config``
  will be seeded with when the user instantiates

When a user picks a template, ``POST /templates/{slug}/instantiate`` creates
a new Workspace pre-populated with the template's domain, then redirects
the user to the upload step.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


@dataclass(frozen=True)
class KBTemplate:
    slug: str
    name: str
    description: str
    category: str
    icon: str  # lucide-react icon name (frontend maps this to a component)
    domain: dict  # the five DomainProfile fields


def _parse(path: Path) -> Optional[KBTemplate]:
    """Read one YAML file into a KBTemplate; log + skip on any error."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        slug = str(raw.get("slug") or path.stem)
        domain = raw.get("domain") or {}
        for required in ("domain_name", "domain_display_name", "organization_name",
                         "knowledge_focus_examples", "tool_focus_examples"):
            if required not in domain:
                logger.warning("template %s missing domain.%s; skipping", slug, required)
                return None
        return KBTemplate(
            slug=slug,
            name=str(raw.get("name") or slug),
            description=str(raw.get("description") or ""),
            category=str(raw.get("category") or "general"),
            icon=str(raw.get("icon") or "Folder"),
            domain=dict(domain),
        )
    except Exception as exc:
        logger.warning("failed to parse template %s: %s", path, exc)
        return None


@lru_cache(maxsize=1)
def list_templates() -> list[KBTemplate]:
    """Return the full catalog — alphabetical by category, then name."""
    if not TEMPLATES_DIR.is_dir():
        logger.info("templates directory %s not found; catalog empty.", TEMPLATES_DIR)
        return []
    out: list[KBTemplate] = []
    for p in sorted(TEMPLATES_DIR.glob("*.yaml")):
        t = _parse(p)
        if t is not None:
            out.append(t)
    return sorted(out, key=lambda t: (t.category, t.name))


def get_template(slug: str) -> Optional[KBTemplate]:
    for t in list_templates():
        if t.slug == slug:
            return t
    return None


def search_templates(query: Optional[str] = None, category: Optional[str] = None) -> list[KBTemplate]:
    """Simple substring filter — sufficient for ~6 code-shipped templates."""
    items = list_templates()
    if category:
        items = [t for t in items if t.category == category]
    if query:
        q = query.lower().strip()
        if q:
            items = [
                t for t in items
                if q in t.name.lower() or q in t.description.lower() or q in t.slug.lower()
            ]
    return items
