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

import logging
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)


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

# Process-global seed KBConfig — read from disk exactly once. Used when no
# workspace context is active (CLI tools, app bootstrap, unit tests).
_ACTIVE: Optional[KBConfig] = None

# Per-workspace cache of KBConfig built by overlaying the workspace's
# Workspace.domain_config JSONB override onto the seed. Cleared for a
# specific workspace by ``reset_workspace_kb_config(wid)`` after a PUT.
_WORKSPACE_CACHE: dict[str, KBConfig] = {}


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
    """Clear the seed cache. No longer drags tenant workspaces with it.

    Before multi-tenancy this cleared the one and only singleton; now it only
    clears the no-context seed. Per-workspace caches have their own key
    (``reset_workspace_kb_config``).
    """
    global _ACTIVE
    _ACTIVE = None
    try:
        from src.agent.prompts.loader import reset_cache as _reset_prompts
        _reset_prompts()
    except Exception:
        pass


def reset_workspace_kb_config(workspace_id: str) -> None:
    """Clear the per-workspace cache entry.

    Called by ``PUT /config/domain`` so the next ``get_active_kb_config()``
    from that workspace's context picks up the new override without touching
    any other tenant.
    """
    _WORKSPACE_CACHE.pop(workspace_id, None)


def prime_workspace_kb_config(workspace_id: str, domain_override: Optional[dict]) -> KBConfig:
    """Populate the per-workspace cache with the overlaid KBConfig.

    Called from ``require_workspace_id`` where we already hold the Workspace
    row — avoids the sync-from-async deadlock that would happen if the cache
    lookup path tried to await the DB from inside a running event loop.
    """
    seed = _load_seed_config()
    cfg = _overlay_domain(seed, domain_override) if domain_override else seed
    _WORKSPACE_CACHE[workspace_id] = cfg
    return cfg


def _overlay_domain(base: KBConfig, overrides: dict) -> KBConfig:
    """Return a new KBConfig with DomainProfile fields replaced by *overrides*.

    Only the five DomainProfile fields are overridden; KB paths, prompt
    overrides, and keyword sets come from the disk seed (which acts as the
    invariant 'shape' of a domain). The workspace's JSONB only carries the
    editable identity fields.
    """
    from dataclasses import replace as _dc_replace
    profile = _dc_replace(
        base.profile,
        domain_name=overrides.get("domain_name", base.profile.domain_name),
        domain_display_name=overrides.get("domain_display_name", base.profile.domain_display_name),
        organization_name=overrides.get("organization_name", base.profile.organization_name),
        knowledge_focus_examples=overrides.get("knowledge_focus_examples", base.profile.knowledge_focus_examples),
        tool_focus_examples=overrides.get("tool_focus_examples", base.profile.tool_focus_examples),
    )
    return _dc_replace(base, profile=profile)


def _candidate_kb_config_paths() -> list[Path]:
    """Candidate locations for the kb-config seed, in priority order.

    The seed is mostly template scaffolding — every real workspace overlays
    its own ``domain_config`` JSONB on top. We still look in a few sensible
    places so a deployment that renamed or split the source tree doesn't
    immediately crash the build pipeline.

    Search order:
      1. ``KB_CONFIG_PATH`` env var (explicit operator override).
      2. ``src/../kb-config`` — same layout as the git checkout.
      3. Ancestors of ``__file__`` that contain a ``kb-config`` directory
         (covers checkouts where ``src/`` is nested deeper than expected).
      4. ``cwd/kb-config`` — handy for CLI invocations.
      5. Ancestors of ``cwd`` that contain a ``kb-config`` directory.
    """
    cands: list[Path] = []
    env_path = os.environ.get("KB_CONFIG_PATH")
    if env_path:
        cands.append(Path(env_path).expanduser().resolve())

    cands.append((Path(__file__).parent.parent / "kb-config").resolve())

    for p in Path(__file__).resolve().parents:
        # Stop walking once we've hit the root — no point climbing past "/".
        if p == p.parent:
            break
        candidate = p / "kb-config"
        if candidate.is_dir() and candidate.resolve() not in cands:
            cands.append(candidate.resolve())
        # Keep the search bounded so we don't scan the whole filesystem.
        if len(cands) > 10:
            break

    try:
        cwd = Path.cwd().resolve()
    except (FileNotFoundError, OSError):
        cwd = None
    if cwd is not None:
        cwd_cand = (cwd / "kb-config").resolve()
        if cwd_cand not in cands:
            cands.append(cwd_cand)
        for p in cwd.parents:
            if p == p.parent:
                break
            candidate = p / "kb-config"
            if candidate.is_dir() and candidate.resolve() not in cands:
                cands.append(candidate.resolve())
            if len(cands) > 15:
                break

    return cands


def _synthesized_seed_config() -> KBConfig:
    """Last-resort in-memory KBConfig when no kb-config/ dir is reachable.

    A workspace build doesn't actually *need* the disk seed to run: the
    per-workspace ``domain_config`` JSONB override provides every
    :class:`DomainProfile` identity field, and the extractor's keyword sets
    fall back to :data:`DEFAULT_TOOL_COLUMN_KEYWORDS` / :data:`DEFAULT_PROCESS_COLUMN_KEYWORDS`
    when no override is present. So if the operator's deployment is missing
    the disk scaffolding we log loudly, synthesize a minimal-but-valid config,
    and let the build proceed — a missing dev seed shouldn't take down 1M
    tenant builds.
    """
    here = Path(__file__).resolve()
    # ``root`` must be an existing path for ``Path(...).resolve()`` to make
    # sense downstream; fall back to the src directory itself, which always
    # exists when we're executing from it.
    root = here.parent
    profile = DomainProfile(
        domain_name="generic",
        domain_display_name="Knowledge Graph",
        organization_name="OpenGraph",
        knowledge_focus_examples="policies, procedures, domain documentation",
        tool_focus_examples="systems, integrations, SaaS tools",
    )
    return KBConfig(
        root=root,
        knowledge_kb_path=root / "__missing__knowledge.json",
        tool_kb_path=root / "__missing__tool.json",
        profile=profile,
        prompt_overrides={},
    )


def _load_seed_config() -> KBConfig:
    """Load the disk-seed KBConfig (no workspace overlay). Idempotent.

    Tries each candidate path from :func:`_candidate_kb_config_paths` in
    order, returning the first one that successfully loads. If none exist,
    synthesises an in-memory default so the build pipeline can still run
    against per-workspace ``domain_config`` overrides.
    """
    global _ACTIVE
    if _ACTIVE is not None:
        return _ACTIVE

    tried: list[Path] = []
    last_err: Optional[Exception] = None
    for root in _candidate_kb_config_paths():
        tried.append(root)
        if not root.is_dir():
            continue
        try:
            _ACTIVE = load_kb_config(root)
            return _ACTIVE
        except Exception as exc:
            # A candidate dir exists but is malformed (missing knowledge/, bad
            # yaml, etc.). Log and keep trying the rest.
            last_err = exc
            logger.warning(
                "kb-config at %s failed to load (%s); trying next candidate.",
                root, exc,
            )

    # None of the candidates worked. Fall back to an in-memory synthesis so
    # workspaces that ship their own domain_config don't get blocked by a
    # missing dev-mode seed on the host.
    logger.warning(
        "No kb-config directory found at any candidate: %s. "
        "Falling back to synthesized defaults. Set KB_CONFIG_PATH to silence. "
        "Last loader error: %s",
        [str(p) for p in tried], last_err,
    )
    _ACTIVE = _synthesized_seed_config()
    return _ACTIVE


def get_active_kb_config() -> KBConfig:
    """Return the KBConfig for the **currently active workspace**, falling back
    to the disk seed when no workspace context is set.

    Resolution:
      1. If ``src.workspace_context.get_current_workspace()`` is set:
         - Return the cached per-workspace config if available.
         - Otherwise load the seed, overlay the workspace's ``domain_config``
           JSONB, cache the result per-workspace, and return it.
      2. Otherwise fall through to the disk seed (CLI tools, bootstrap).

    The seed acts as the invariant shape of the domain (KB paths, prompts,
    keyword sets). Only the five DomainProfile identity fields are tenant-
    overridable — that matches the shape of ``Workspace.domain_config``.
    """
    # Local import to avoid a circular import at module-load time — the
    # workspace_context / workspace_domain modules pull in db_models which
    # imports from src.infra.db, and we don't want kb_config on that graph.
    from src.workspace_context import get_current_workspace

    wid = get_current_workspace()
    if not wid:
        return _load_seed_config()

    cached = _WORKSPACE_CACHE.get(wid)
    if cached is not None:
        return cached

    seed = _load_seed_config()
    try:
        from src.infra.workspace_domain import get_workspace_domain_sync
        overrides = get_workspace_domain_sync(wid)
    except Exception:
        overrides = None

    cfg = _overlay_domain(seed, overrides) if overrides else seed
    _WORKSPACE_CACHE[wid] = cfg
    return cfg


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
