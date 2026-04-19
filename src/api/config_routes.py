"""
Config CRUD endpoints for kb-config/domain.yaml and kb-config/graph.yaml.

- GET  /config/domain -> current DomainProfile fields
- PUT  /config/domain -> write domain.yaml and clear active-config cache
- GET  /config/graph  -> full GraphConfig JSON (6 sections, ~25 knobs)
- PUT  /config/graph  -> write graph.yaml and clear graph_config cache

Writes go to the active kb-config root (cfg.root from get_active_kb_config()).
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.graph_config import GraphConfig, get_graph_config
from src.kb_config import get_active_kb_config, reset_active_kb_config

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Domain
# ---------------------------------------------------------------------------

class DomainPayload(BaseModel):
    domain_name: str
    domain_display_name: str
    organization_name: str
    knowledge_focus_examples: str
    tool_focus_examples: str


@router.get("/config/domain", response_model=DomainPayload, summary="Read domain.yaml")
async def get_domain():
    cfg = get_active_kb_config()
    p = cfg.profile
    return DomainPayload(
        domain_name=p.domain_name,
        domain_display_name=p.domain_display_name,
        organization_name=p.organization_name,
        knowledge_focus_examples=p.knowledge_focus_examples,
        tool_focus_examples=p.tool_focus_examples,
    )


@router.put("/config/domain", summary="Write domain.yaml")
async def put_domain(payload: DomainPayload):
    cfg = get_active_kb_config()
    path = cfg.root / "domain.yaml"

    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            with open(path, encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
            if not isinstance(existing, dict):
                existing = {}
        except Exception as exc:
            logger.warning("Could not parse existing domain.yaml (%s); overwriting.", exc)
            existing = {}

    existing.update(payload.model_dump())

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(existing, f, sort_keys=False, allow_unicode=True)

    reset_active_kb_config()
    return {"ok": True, "path": str(path)}


# ---------------------------------------------------------------------------
# Graph config
# ---------------------------------------------------------------------------

# Rebuild-triggering sections vs. live-effect sections.
_REBUILD_SECTIONS = {"embeddings", "edges", "cross_kb", "extraction"}
_LIVE_SECTIONS = {"traversal", "search"}


class EmbeddingsPayload(BaseModel):
    model: str
    dimensions: Optional[int] = None
    tfidf_fallback_dim: int
    similarity_threshold: float = Field(ge=0.0, le=1.0)
    max_related_edges_per_node: int = Field(ge=0, le=200)
    skip_related_to_types: list[str]
    input_max_chars: int = Field(ge=16, le=8192)
    local_batch_size: int = Field(ge=1, le=1024)
    openrouter_batch_size: int = Field(ge=1, le=1024)


class EdgeWeightsPayload(BaseModel):
    contains: float
    next_step: float
    integrates_with: float
    implements: float
    uses_tool: float


class EdgesPayload(BaseModel):
    weights: EdgeWeightsPayload
    min_tool_mention_length: int = Field(ge=1, le=64)
    enable_tool_name_aliases: bool


class CrossKbPayload(BaseModel):
    auto_threshold: float = Field(ge=0.0, le=1.0)
    embed_weight: float = Field(ge=0.0, le=1.0)
    cooccur_weight: float = Field(ge=0.0, le=1.0)
    max_links_per_chapter: int = Field(ge=0, le=50)
    chapter_embedding_max_chars: int = Field(ge=16, le=16384)
    min_tool_name_length: int = Field(ge=1, le=64)
    skip_chapter_headings: list[str]
    llm_max_tokens: int = Field(ge=64, le=16384)


class TraversalPayload(BaseModel):
    max_depth: int = Field(ge=1, le=20)
    max_nodes: int = Field(ge=1, le=2000)
    subtree_max_depth: int = Field(ge=1, le=10)
    full_tree_depth: int = Field(ge=1, le=10)
    top_k_entry_nodes: int = Field(ge=1, le=100)


class SearchPayload(BaseModel):
    default_top_k: int = Field(ge=1, le=200)
    keyword_min_token_length: int = Field(ge=1, le=20)
    hybrid_semantic_weight: float = Field(ge=0.0, le=1.0)
    hybrid_keyword_weight: float = Field(ge=0.0, le=1.0)


class ExtractionPayload(BaseModel):
    section_summary_max_chars: int = Field(ge=16, le=8192)
    max_paragraphs_per_section: int = Field(ge=1, le=200)
    toolnode_summary_max_chars: int = Field(ge=16, le=4096)
    processnode_summary_max_chars: int = Field(ge=16, le=4096)
    glossarynode_summary_max_chars: int = Field(ge=16, le=4096)
    tablenode_summary_max_headers: int = Field(ge=1, le=100)
    max_slug_length: int = Field(ge=4, le=200)


class GraphConfigPayload(BaseModel):
    embeddings: EmbeddingsPayload
    edges: EdgesPayload
    cross_kb: CrossKbPayload
    traversal: TraversalPayload
    search: SearchPayload
    extraction: ExtractionPayload


def _graph_config_to_payload(gc: GraphConfig) -> GraphConfigPayload:
    return GraphConfigPayload(
        embeddings=EmbeddingsPayload(
            model=gc.embeddings.model,
            dimensions=gc.embeddings.dimensions,
            tfidf_fallback_dim=gc.embeddings.tfidf_fallback_dim,
            similarity_threshold=gc.embeddings.similarity_threshold,
            max_related_edges_per_node=gc.embeddings.max_related_edges_per_node,
            skip_related_to_types=list(gc.embeddings.skip_related_to_types),
            input_max_chars=gc.embeddings.input_max_chars,
            local_batch_size=gc.embeddings.local_batch_size,
            openrouter_batch_size=gc.embeddings.openrouter_batch_size,
        ),
        edges=EdgesPayload(
            weights=EdgeWeightsPayload(**asdict(gc.edges.weights)),
            min_tool_mention_length=gc.edges.min_tool_mention_length,
            enable_tool_name_aliases=gc.edges.enable_tool_name_aliases,
        ),
        cross_kb=CrossKbPayload(
            auto_threshold=gc.cross_kb.auto_threshold,
            embed_weight=gc.cross_kb.embed_weight,
            cooccur_weight=gc.cross_kb.cooccur_weight,
            max_links_per_chapter=gc.cross_kb.max_links_per_chapter,
            chapter_embedding_max_chars=gc.cross_kb.chapter_embedding_max_chars,
            min_tool_name_length=gc.cross_kb.min_tool_name_length,
            skip_chapter_headings=list(gc.cross_kb.skip_chapter_headings),
            llm_max_tokens=gc.cross_kb.llm_max_tokens,
        ),
        traversal=TraversalPayload(
            max_depth=gc.traversal.max_depth,
            max_nodes=gc.traversal.max_nodes,
            subtree_max_depth=gc.traversal.subtree_max_depth,
            full_tree_depth=gc.traversal.full_tree_depth,
            top_k_entry_nodes=gc.traversal.top_k_entry_nodes,
        ),
        search=SearchPayload(
            default_top_k=gc.search.default_top_k,
            keyword_min_token_length=gc.search.keyword_min_token_length,
            hybrid_semantic_weight=gc.search.hybrid_semantic_weight,
            hybrid_keyword_weight=gc.search.hybrid_keyword_weight,
        ),
        extraction=ExtractionPayload(
            section_summary_max_chars=gc.extraction.section_summary_max_chars,
            max_paragraphs_per_section=gc.extraction.max_paragraphs_per_section,
            toolnode_summary_max_chars=gc.extraction.toolnode_summary_max_chars,
            processnode_summary_max_chars=gc.extraction.processnode_summary_max_chars,
            glossarynode_summary_max_chars=gc.extraction.glossarynode_summary_max_chars,
            tablenode_summary_max_headers=gc.extraction.tablenode_summary_max_headers,
            max_slug_length=gc.extraction.max_slug_length,
        ),
    )


def _payload_to_yaml_dict(p: GraphConfigPayload) -> dict:
    return {
        "embeddings": p.embeddings.model_dump(),
        "edges": {
            "weights": p.edges.weights.model_dump(),
            "min_tool_mention_length": p.edges.min_tool_mention_length,
            "enable_tool_name_aliases": p.edges.enable_tool_name_aliases,
        },
        "cross_kb": p.cross_kb.model_dump(),
        "traversal": p.traversal.model_dump(),
        "search": p.search.model_dump(),
        "extraction": p.extraction.model_dump(),
    }


@router.get("/config/graph", response_model=GraphConfigPayload, summary="Read graph.yaml")
async def get_graph_cfg():
    # Clear cache so any file edits on disk are reflected.
    get_graph_config.cache_clear()
    gc = get_graph_config()
    return _graph_config_to_payload(gc)


class PutGraphConfigResponse(BaseModel):
    status: str
    path: str
    changed_sections: list[str]
    requires_rebuild: bool


@router.put("/config/graph", response_model=PutGraphConfigResponse, summary="Write graph.yaml")
async def put_graph_cfg(payload: GraphConfigPayload):
    cfg = get_active_kb_config()
    # Write to the active kb-config root to respect KB_CONFIG_PATH overrides.
    # The loader at src/graph_config.py reads a fixed path, so we also mirror
    # there if different.
    target = cfg.root / "graph.yaml"

    # Compute changed sections vs current
    current = _graph_config_to_payload(get_graph_config())
    changed: list[str] = []
    for section in ("embeddings", "edges", "cross_kb", "traversal", "search", "extraction"):
        if getattr(current, section).model_dump() != getattr(payload, section).model_dump():
            changed.append(section)

    data = _payload_to_yaml_dict(payload)

    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)

    # Also mirror to the repo-default path that graph_config.py reads.
    fallback_path = Path(__file__).parent.parent.parent / "kb-config" / "graph.yaml"
    try:
        if fallback_path.resolve() != target.resolve():
            fallback_path.parent.mkdir(parents=True, exist_ok=True)
            with open(fallback_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
    except Exception as exc:
        logger.warning("Mirror write of graph.yaml to %s failed: %s", fallback_path, exc)

    get_graph_config.cache_clear()

    requires_rebuild = any(s in _REBUILD_SECTIONS for s in changed)
    status = "requires_rebuild" if requires_rebuild else "applied"
    return PutGraphConfigResponse(
        status=status,
        path=str(target),
        changed_sections=changed,
        requires_rebuild=requires_rebuild,
    )
