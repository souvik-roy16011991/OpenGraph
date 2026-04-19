"""
Domain-agnostic prompt package.

Public API
----------
get_domain_prompts()      -- DomainPrompts for the currently active kb-config
DomainPrompts             -- typed prompt container
build_domain_prompts(...) -- factory that fills base templates with domain values
format_context_for_llm()  -- domain-agnostic context formatter utility

Per-kb-config prompt customization
----------------------------------
Drop any of these Markdown files inside your kb-config folder to override a
single prompt slot verbatim (the rest still come from BASE_* templates):

    <kb-config>/prompts/intent_system.md
    <kb-config>/prompts/intent_template.md
    <kb-config>/prompts/synthesize_system.md
    <kb-config>/prompts/synthesize_explore.md
    <kb-config>/prompts/synthesize_process.md
    <kb-config>/prompts/synthesize_tool.md
    <kb-config>/prompts/synthesize_compare.md
"""

from .base import DomainPrompts, build_domain_prompts, format_context_for_llm
from .loader import get_domain_prompts, reset_cache

__all__ = [
    "DomainPrompts",
    "build_domain_prompts",
    "format_context_for_llm",
    "get_domain_prompts",
    "reset_cache",
]
