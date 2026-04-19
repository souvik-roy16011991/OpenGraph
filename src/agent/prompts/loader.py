"""
Runtime DomainPrompts loader.

Reads the active KBConfig — profile + per-slot prompt overrides — and builds a
DomainPrompts via ``build_domain_prompts()``.  Result is cached per active
config root so repeated calls are free.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from src.kb_config import KBConfig, get_active_kb_config

from .base import DomainPrompts, build_domain_prompts


def get_domain_prompts() -> DomainPrompts:
    """Return DomainPrompts for the currently active kb-config."""
    cfg = get_active_kb_config()
    return _build(cfg.root)


@lru_cache(maxsize=8)
def _build(root_key: Path) -> DomainPrompts:
    # Re-read the active config so the cache key is the root path; any other
    # fields are resolved through the live cfg at call time.
    cfg: KBConfig = get_active_kb_config()
    ov = cfg.prompt_overrides
    p = cfg.profile
    return build_domain_prompts(
        domain_name=p.domain_name,
        domain_display_name=p.domain_display_name,
        organization_name=p.organization_name,
        knowledge_focus_examples=p.knowledge_focus_examples,
        tool_focus_examples=p.tool_focus_examples,
        intent_classification_system=ov.get("intent_system"),
        intent_classification_template=ov.get("intent_template"),
        synthesize_system=ov.get("synthesize_system"),
        synthesize_process_template=ov.get("synthesize_process"),
        synthesize_explore_template=ov.get("synthesize_explore"),
        synthesize_tool_template=ov.get("synthesize_tool"),
        synthesize_compare_template=ov.get("synthesize_compare"),
    )


def reset_cache() -> None:
    """Drop the cached DomainPrompts — call after ``set_active_kb_config()``."""
    _build.cache_clear()
