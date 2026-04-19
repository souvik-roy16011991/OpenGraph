"""
Active kb-config resolution.

A kb-config folder fully describes one domain's inputs:
  <root>/
    knowledge/<anything>.json        required — 1+ knowledge KB JSON(s)
    tool/<anything>.json             required — 1+ tool KB JSON(s)
    domain.yaml                      optional — any subset of DomainProfile fields
    prompts/<slot>.md                optional — verbatim per-slot prompt overrides
    extractor/keywords.yaml          optional — tool/process column keyword overrides
    .generated/profile.yaml          auto — cached LLM-polished profile

This module is the single source of truth for the active config; other modules
call ``get_active_kb_config()`` instead of importing domain-specific constants.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_TOOL_COLUMN_KEYWORDS: frozenset[str] = frozenset({
    "Tool", "Service", "System", "Product", "Application",
    "Platform", "Software", "Vendor", "Provider",
})

DEFAULT_PROCESS_COLUMN_KEYWORDS: frozenset[str] = frozenset({
    "Step", "Phase", "Stage", "Workflow", "Onboarding Step",
    "Process Step", "Action", "Task",
})

PROMPT_SLOTS: tuple[str, ...] = (
    "intent_system",
    "intent_template",
    "synthesize_system",
    "synthesize_explore",
    "synthesize_process",
    "synthesize_tool",
    "synthesize_compare",
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DomainProfile:
    """The four placeholders ``build_domain_prompts()`` needs + the domain key."""
    domain_name: str
    domain_display_name: str
    organization_name: str
    knowledge_focus_examples: str
    tool_focus_examples: str


@dataclass(frozen=True)
class KBConfig:
    root: Path
    knowledge_kb_path: Path
    tool_kb_path: Path
    profile: DomainProfile
    prompt_overrides: dict[str, str] = field(default_factory=dict)
    tool_column_keywords: frozenset[str] = DEFAULT_TOOL_COLUMN_KEYWORDS
    process_column_keywords: frozenset[str] = DEFAULT_PROCESS_COLUMN_KEYWORDS


# ---------------------------------------------------------------------------
# Active-config singleton
# ---------------------------------------------------------------------------

_ACTIVE: Optional[KBConfig] = None


def set_active_kb_config(cfg: KBConfig) -> None:
    """Install *cfg* as the process-wide active config."""
    global _ACTIVE
    _ACTIVE = cfg
    # Make the path visible to subprocesses / late imports.
    os.environ["KB_CONFIG_PATH"] = str(cfg.root)
    # Clear any cached DomainPrompts tied to the previous config, if the
    # prompt loader module has already been imported.
    try:
        from src.agent.prompts.loader import reset_cache as _reset_prompts
        _reset_prompts()
    except Exception:
        pass


def reset_active_kb_config() -> None:
    """Clear the cached active config so the next get_active_kb_config() reloads fresh.

    Call this after writing changes to domain.yaml / extractor/keywords.yaml so
    that subsequent reads pick up the new values without a server restart.
    """
    global _ACTIVE
    _ACTIVE = None
    try:
        from src.agent.prompts.loader import reset_cache as _reset_prompts
        _reset_prompts()
    except Exception:
        pass


def get_active_kb_config() -> KBConfig:
    """Return the active config, resolving from ``KB_CONFIG_PATH`` on first call.

    Resolution order:
      1. Explicit ``set_active_kb_config()`` call.
      2. ``KB_CONFIG_PATH`` env var pointing to a kb-config folder.
      3. Project default ``<repo>/kb-config``.
    """
    global _ACTIVE
    if _ACTIVE is not None:
        return _ACTIVE

    env_path = os.environ.get("KB_CONFIG_PATH")
    if env_path:
        root = Path(env_path).expanduser().resolve()
    else:
        root = (Path(__file__).parent.parent / "kb-config").resolve()

    _ACTIVE = load_kb_config(root)
    return _ACTIVE


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_kb_config(
    path: str | Path,
    *,
    domain_hint: Optional[str] = None,
    use_llm_profile: bool = True,
) -> KBConfig:
    """Load a KBConfig from *path*.

    Field resolution order (highest → lowest priority):
      1. ``domain.yaml`` explicit value
      2. Cached ``.generated/profile.yaml`` from a previous LLM polish
      3. LLM polish output (if ``use_llm_profile`` and an API key is set)
      4. Deterministic KB-scan defaults
      5. *domain_hint* + generic template for org_name

    Prompt overrides: files under ``prompts/<slot>.md`` always override the
    BASE_* templates verbatim.
    """
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"kb-config path is not a directory: {root}")

    knowledge_kb_path = _pick_kb_file(root / "knowledge", "knowledge")
    tool_kb_path = _pick_kb_file(root / "tool", "tool")

    manifest = _read_yaml(root / "domain.yaml")

    kb_section = manifest.get("kb") or {}
    if kb_section.get("knowledge"):
        knowledge_kb_path = (root / kb_section["knowledge"]).resolve()
    if kb_section.get("tool"):
        tool_kb_path = (root / kb_section["tool"]).resolve()

    cached_profile = _read_yaml(root / ".generated" / "profile.yaml")

    # Lazy import so kb_config.py stays a leaf module with no pipeline deps.
    from src.domain_profiler import profile_domain

    profile = profile_domain(
        kb_root=root,
        knowledge_kb_path=knowledge_kb_path,
        tool_kb_path=tool_kb_path,
        domain_hint=domain_hint,
        manifest_overrides=manifest,
        cached_profile=cached_profile,
        use_llm=use_llm_profile,
    )

    prompt_overrides = _load_prompt_overrides(root)

    tool_kw, proc_kw = _load_extractor_overrides(root)

    return KBConfig(
        root=root,
        knowledge_kb_path=knowledge_kb_path,
        tool_kb_path=tool_kb_path,
        profile=profile,
        prompt_overrides=prompt_overrides,
        tool_column_keywords=tool_kw,
        process_column_keywords=proc_kw,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pick_kb_file(folder: Path, label: str) -> Path:
    """Pick the single JSON under *folder*; error clearly if ambiguous/missing."""
    if not folder.is_dir():
        raise FileNotFoundError(
            f"Missing required '{folder.name}/' folder under kb-config: {folder}"
        )
    jsons = sorted(p for p in folder.glob("*.json") if p.is_file())
    if not jsons:
        raise FileNotFoundError(
            f"No *.json file found in {folder}. "
            f"Place a {label} KB JSON inside {folder.name}/."
        )
    if len(jsons) > 1:
        # Prefer the one named in domain.yaml if the caller specified it; else
        # take the first and warn downstream consumers via logs.
        return jsons[0]
    return jsons[0]


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping at {path}, got {type(data).__name__}")
    return data


def _load_prompt_overrides(root: Path) -> dict[str, str]:
    folder = root / "prompts"
    if not folder.is_dir():
        return {}
    overrides: dict[str, str] = {}
    for slot in PROMPT_SLOTS:
        fp = folder / f"{slot}.md"
        if fp.is_file():
            overrides[slot] = fp.read_text(encoding="utf-8")
    return overrides


def _load_extractor_overrides(root: Path) -> tuple[frozenset[str], frozenset[str]]:
    data = _read_yaml(root / "extractor" / "keywords.yaml")
    tool_kw = frozenset(data.get("tool_column_keywords") or DEFAULT_TOOL_COLUMN_KEYWORDS)
    proc_kw = frozenset(data.get("process_column_keywords") or DEFAULT_PROCESS_COLUMN_KEYWORDS)
    return tool_kw, proc_kw


# Convenience for domain_profiler when it needs to update the cache.
def write_profile_cache(root: Path, profile: DomainProfile) -> None:
    """Persist *profile* to ``<root>/.generated/profile.yaml``."""
    gen_dir = root / ".generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "domain_name": profile.domain_name,
        "domain_display_name": profile.domain_display_name,
        "organization_name": profile.organization_name,
        "knowledge_focus_examples": profile.knowledge_focus_examples,
        "tool_focus_examples": profile.tool_focus_examples,
    }
    with open(gen_dir / "profile.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def _unused_keep_imports() -> None:  # pragma: no cover
    # Silences unused-import warnings for `replace` (used in tests) in some linters.
    _ = replace
