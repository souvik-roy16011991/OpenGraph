"""
Graph construction & query configuration loader.

Reads kb-config/graph.yaml into a frozen GraphConfig dataclass. The rest of
the codebase imports ``get_graph_config()`` and reads attributes off the
returned object.

Precedence (highest wins):
  1. Environment variable for the legacy names (SIMILARITY_THRESHOLD, etc.)
     — preserved for backwards compatibility with existing deployments.
  2. The matching value in kb-config/graph.yaml.
  3. The built-in default encoded below.

If kb-config/graph.yaml is missing, malformed, or incomplete, only the built-in
defaults are used — the loader never raises.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

_GRAPH_YAML_PATH = Path(__file__).parent.parent / "kb-config" / "graph.yaml"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EmbeddingsCfg:
    model: str = "qwen/qwen3-embedding-8b"
    dimensions: Optional[int] = None
    tfidf_fallback_dim: int = 4096
    similarity_threshold: float = 0.80
    max_related_edges_per_node: int = 8
    skip_related_to_types: tuple[str, ...] = ("glossary",)
    input_max_chars: int = 512
    local_batch_size: int = 64
    openrouter_batch_size: int = 32


@dataclass(frozen=True)
class EdgeWeights:
    contains: float = 1.0
    next_step: float = 1.0
    integrates_with: float = 0.9
    implements: float = 0.8
    uses_tool: float = 0.7


@dataclass(frozen=True)
class EdgesCfg:
    weights: EdgeWeights = field(default_factory=EdgeWeights)
    min_tool_mention_length: int = 5
    enable_tool_name_aliases: bool = True


@dataclass(frozen=True)
class CrossKbCfg:
    auto_threshold: float = 0.35
    embed_weight: float = 0.6
    cooccur_weight: float = 0.4
    max_links_per_chapter: int = 5
    chapter_embedding_max_chars: int = 500
    min_tool_name_length: int = 4
    skip_chapter_headings: tuple[str, ...] = ("Table of Contents",)
    llm_max_tokens: int = 2048


@dataclass(frozen=True)
class TraversalCfg:
    max_depth: int = 5
    max_nodes: int = 40
    subtree_max_depth: int = 3
    full_tree_depth: int = 2
    top_k_entry_nodes: int = 6


@dataclass(frozen=True)
class SearchCfg:
    default_top_k: int = 10
    keyword_min_token_length: int = 3
    hybrid_semantic_weight: float = 0.6
    hybrid_keyword_weight: float = 0.4


@dataclass(frozen=True)
class ExtractionCfg:
    section_summary_max_chars: int = 500
    max_paragraphs_per_section: int = 10
    toolnode_summary_max_chars: int = 300
    processnode_summary_max_chars: int = 300
    glossarynode_summary_max_chars: int = 300
    tablenode_summary_max_headers: int = 6
    max_slug_length: int = 30


@dataclass(frozen=True)
class GraphConfig:
    embeddings: EmbeddingsCfg = field(default_factory=EmbeddingsCfg)
    edges: EdgesCfg = field(default_factory=EdgesCfg)
    cross_kb: CrossKbCfg = field(default_factory=CrossKbCfg)
    traversal: TraversalCfg = field(default_factory=TraversalCfg)
    search: SearchCfg = field(default_factory=SearchCfg)
    extraction: ExtractionCfg = field(default_factory=ExtractionCfg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _env_float(name: str) -> Optional[float]:
    raw = os.environ.get(name, "")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("Ignoring non-numeric %s=%r", name, raw)
        return None


def _env_int(name: str) -> Optional[int]:
    raw = os.environ.get(name, "")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("Ignoring non-integer %s=%r", name, raw)
        return None


def _merge(yaml_block: dict[str, Any] | None, defaults: dict[str, Any]) -> dict[str, Any]:
    out = dict(defaults)
    if isinstance(yaml_block, dict):
        for k, v in yaml_block.items():
            if k in out:
                out[k] = v
    return out


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_graph_config() -> GraphConfig:
    raw: dict[str, Any] = {}
    if _GRAPH_YAML_PATH.exists():
        try:
            with open(_GRAPH_YAML_PATH, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            if not isinstance(raw, dict):
                logger.warning("%s is not a mapping; ignoring.", _GRAPH_YAML_PATH)
                raw = {}
        except Exception as exc:
            logger.warning("Failed to parse %s (%s); using defaults.", _GRAPH_YAML_PATH, exc)
            raw = {}
    else:
        logger.debug("%s not found; using built-in graph defaults.", _GRAPH_YAML_PATH)

    emb_defaults = EmbeddingsCfg()
    emb_raw = _merge(raw.get("embeddings"), {
        "model": emb_defaults.model,
        "dimensions": emb_defaults.dimensions,
        "tfidf_fallback_dim": emb_defaults.tfidf_fallback_dim,
        "similarity_threshold": emb_defaults.similarity_threshold,
        "max_related_edges_per_node": emb_defaults.max_related_edges_per_node,
        "skip_related_to_types": list(emb_defaults.skip_related_to_types),
        "input_max_chars": emb_defaults.input_max_chars,
        "local_batch_size": emb_defaults.local_batch_size,
        "openrouter_batch_size": emb_defaults.openrouter_batch_size,
    })
    # Env-var overrides for legacy names
    emb_raw["similarity_threshold"] = _env_float("SIMILARITY_THRESHOLD") or emb_raw["similarity_threshold"]
    emb_raw["max_related_edges_per_node"] = _env_int("MAX_RELATED_EDGES_PER_NODE") or emb_raw["max_related_edges_per_node"]
    model_env = os.environ.get("EMBEDDING_MODEL", "")
    if model_env:
        emb_raw["model"] = model_env
    dims_env = os.environ.get("EMBEDDING_DIMENSIONS", "")
    if dims_env:
        try:
            emb_raw["dimensions"] = int(dims_env)
        except ValueError:
            pass
    tfidf_env = _env_int("EMBEDDING_DIM")
    if tfidf_env is not None:
        emb_raw["tfidf_fallback_dim"] = tfidf_env

    embeddings = EmbeddingsCfg(
        model=str(emb_raw["model"]),
        dimensions=emb_raw["dimensions"] if emb_raw["dimensions"] not in ("", None) else None,
        tfidf_fallback_dim=int(emb_raw["tfidf_fallback_dim"]),
        similarity_threshold=float(emb_raw["similarity_threshold"]),
        max_related_edges_per_node=int(emb_raw["max_related_edges_per_node"]),
        skip_related_to_types=tuple(str(x).lower() for x in (emb_raw["skip_related_to_types"] or [])),
        input_max_chars=int(emb_raw["input_max_chars"]),
        local_batch_size=int(emb_raw["local_batch_size"]),
        openrouter_batch_size=int(emb_raw["openrouter_batch_size"]),
    )

    # Edges
    edge_defaults = EdgesCfg()
    edges_block = raw.get("edges") or {}
    w_defaults = EdgeWeights()
    w_block = edges_block.get("weights") or {}
    weights = EdgeWeights(
        contains=float(w_block.get("contains", w_defaults.contains)),
        next_step=float(w_block.get("next_step", w_defaults.next_step)),
        integrates_with=float(w_block.get("integrates_with", w_defaults.integrates_with)),
        implements=float(w_block.get("implements", w_defaults.implements)),
        uses_tool=float(w_block.get("uses_tool", w_defaults.uses_tool)),
    )
    edges_cfg = EdgesCfg(
        weights=weights,
        min_tool_mention_length=int(edges_block.get("min_tool_mention_length", edge_defaults.min_tool_mention_length)),
        enable_tool_name_aliases=bool(edges_block.get("enable_tool_name_aliases", edge_defaults.enable_tool_name_aliases)),
    )

    # Cross-KB
    xkb_defaults = CrossKbCfg()
    xkb_raw = _merge(raw.get("cross_kb"), {
        "auto_threshold": xkb_defaults.auto_threshold,
        "embed_weight": xkb_defaults.embed_weight,
        "cooccur_weight": xkb_defaults.cooccur_weight,
        "max_links_per_chapter": xkb_defaults.max_links_per_chapter,
        "chapter_embedding_max_chars": xkb_defaults.chapter_embedding_max_chars,
        "min_tool_name_length": xkb_defaults.min_tool_name_length,
        "skip_chapter_headings": list(xkb_defaults.skip_chapter_headings),
        "llm_max_tokens": xkb_defaults.llm_max_tokens,
    })
    xkb_raw["auto_threshold"] = _env_float("CROSS_KB_AUTO_THRESHOLD") or xkb_raw["auto_threshold"]
    xkb_raw["embed_weight"] = _env_float("CROSS_KB_EMBED_WEIGHT") or xkb_raw["embed_weight"]
    xkb_raw["cooccur_weight"] = _env_float("CROSS_KB_COOCCUR_WEIGHT") or xkb_raw["cooccur_weight"]
    xkb_raw["max_links_per_chapter"] = _env_int("CROSS_KB_MAX_LINKS_PER_CHAPTER") or xkb_raw["max_links_per_chapter"]

    cross_kb = CrossKbCfg(
        auto_threshold=float(xkb_raw["auto_threshold"]),
        embed_weight=float(xkb_raw["embed_weight"]),
        cooccur_weight=float(xkb_raw["cooccur_weight"]),
        max_links_per_chapter=int(xkb_raw["max_links_per_chapter"]),
        chapter_embedding_max_chars=int(xkb_raw["chapter_embedding_max_chars"]),
        min_tool_name_length=int(xkb_raw["min_tool_name_length"]),
        skip_chapter_headings=tuple(str(x) for x in (xkb_raw["skip_chapter_headings"] or [])),
        llm_max_tokens=int(xkb_raw["llm_max_tokens"]),
    )

    # Traversal
    trv_defaults = TraversalCfg()
    trv_raw = _merge(raw.get("traversal"), {
        "max_depth": trv_defaults.max_depth,
        "max_nodes": trv_defaults.max_nodes,
        "subtree_max_depth": trv_defaults.subtree_max_depth,
        "full_tree_depth": trv_defaults.full_tree_depth,
        "top_k_entry_nodes": trv_defaults.top_k_entry_nodes,
    })
    trv_raw["max_depth"] = _env_int("MAX_TRAVERSAL_DEPTH") or trv_raw["max_depth"]
    trv_raw["top_k_entry_nodes"] = _env_int("TOP_K_ENTRY_NODES") or trv_raw["top_k_entry_nodes"]
    traversal = TraversalCfg(
        max_depth=int(trv_raw["max_depth"]),
        max_nodes=int(trv_raw["max_nodes"]),
        subtree_max_depth=int(trv_raw["subtree_max_depth"]),
        full_tree_depth=int(trv_raw["full_tree_depth"]),
        top_k_entry_nodes=int(trv_raw["top_k_entry_nodes"]),
    )

    # Search
    sc_defaults = SearchCfg()
    sc_raw = _merge(raw.get("search"), {
        "default_top_k": sc_defaults.default_top_k,
        "keyword_min_token_length": sc_defaults.keyword_min_token_length,
        "hybrid_semantic_weight": sc_defaults.hybrid_semantic_weight,
        "hybrid_keyword_weight": sc_defaults.hybrid_keyword_weight,
    })
    search = SearchCfg(
        default_top_k=int(sc_raw["default_top_k"]),
        keyword_min_token_length=int(sc_raw["keyword_min_token_length"]),
        hybrid_semantic_weight=float(sc_raw["hybrid_semantic_weight"]),
        hybrid_keyword_weight=float(sc_raw["hybrid_keyword_weight"]),
    )

    # Extraction
    ex_defaults = ExtractionCfg()
    ex_raw = _merge(raw.get("extraction"), {
        "section_summary_max_chars": ex_defaults.section_summary_max_chars,
        "max_paragraphs_per_section": ex_defaults.max_paragraphs_per_section,
        "toolnode_summary_max_chars": ex_defaults.toolnode_summary_max_chars,
        "processnode_summary_max_chars": ex_defaults.processnode_summary_max_chars,
        "glossarynode_summary_max_chars": ex_defaults.glossarynode_summary_max_chars,
        "tablenode_summary_max_headers": ex_defaults.tablenode_summary_max_headers,
        "max_slug_length": ex_defaults.max_slug_length,
    })
    extraction = ExtractionCfg(
        section_summary_max_chars=int(ex_raw["section_summary_max_chars"]),
        max_paragraphs_per_section=int(ex_raw["max_paragraphs_per_section"]),
        toolnode_summary_max_chars=int(ex_raw["toolnode_summary_max_chars"]),
        processnode_summary_max_chars=int(ex_raw["processnode_summary_max_chars"]),
        glossarynode_summary_max_chars=int(ex_raw["glossarynode_summary_max_chars"]),
        tablenode_summary_max_headers=int(ex_raw["tablenode_summary_max_headers"]),
        max_slug_length=int(ex_raw["max_slug_length"]),
    )

    return GraphConfig(
        embeddings=embeddings,
        edges=edges_cfg,
        cross_kb=cross_kb,
        traversal=traversal,
        search=search,
        extraction=extraction,
    )
