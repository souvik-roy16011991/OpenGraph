"""
Config CRUD endpoints for domain and graph config — Neon-backed.

- GET  /config/domain -> current DomainProfile fields (from active kb-config seed)
- PUT  /config/domain -> persist a new workspace-scoped config snapshot in Neon
- GET  /config/graph  -> full GraphConfig JSON (6 sections, ~25 knobs)
- PUT  /config/graph  -> persist a new workspace-scoped graph snapshot in Neon

Writes do NOT touch the local filesystem. Every PUT records a
``config_versions`` row keyed to (workspace_id, kind) — that row is the
authoritative record of the config and is surfaced via history endpoints.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from typing import Optional

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.auth import require_user
from src.api.deps import require_workspace_id
from src.config import USE_NEON
from src.infra.audit import record_audit
from src.infra.db_models import User
from src.graph_config import GraphConfig, get_graph_config
from src.kb_config import get_active_kb_config


async def _record_config_version(
    workspace_id: str,
    kind: str,
    yaml_text: str,
    parsed: dict,
    changed_sections: list[str] | None = None,
    requires_rebuild: bool | None = None,
) -> None:
    """Insert a config_versions row scoped to workspace_id; no-op if Neon off."""
    if not USE_NEON:
        return
    try:
        from src.infra.db import get_session
        from src.infra.db_models import ConfigVersion
        async with get_session() as s:
            s.add(ConfigVersion(
                workspace_id=uuid.UUID(workspace_id),
                kind=kind,
                yaml_snapshot=yaml_text,
                parsed_snapshot=parsed,
                changed_sections=changed_sections,
                requires_rebuild=requires_rebuild,
            ))
            await s.commit()
    except Exception as exc:
        logger.warning("Neon config_versions insert failed: %s", exc)

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


@router.get("/config/domain", response_model=DomainPayload, summary="Read the workspace's domain config")
async def get_domain(workspace_id: str = Depends(require_workspace_id)):
    """Return the workspace's DomainProfile.

    Reads ``Workspace.domain_config`` when set, falling back to the disk
    seed for any field the workspace hasn't overridden. The disk seed is
    the neutral default — the workspace override is the truth.
    """
    # get_active_kb_config() is workspace-aware via the contextvar, so the
    # returned KBConfig already reflects the workspace's JSONB overrides.
    cfg = get_active_kb_config()
    p = cfg.profile
    return DomainPayload(
        domain_name=p.domain_name,
        domain_display_name=p.domain_display_name,
        organization_name=p.organization_name,
        knowledge_focus_examples=p.knowledge_focus_examples,
        tool_focus_examples=p.tool_focus_examples,
    )


@router.put("/config/domain", summary="Save the workspace's domain config")
async def put_domain(
    payload: DomainPayload,
    workspace_id: str = Depends(require_workspace_id),
    user: User = Depends(require_user),
):
    """Persist the DomainProfile override on ``Workspace.domain_config``.

    Also records an audit row in ``config_versions`` (via
    ``_record_config_version``). Invalidates the per-workspace KBConfig
    cache so the next request for this workspace sees the new values.
    Does NOT clear other workspaces' caches — that was the old singleton bug.
    """
    if not USE_NEON:
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL (Neon) is required to persist config changes.",
        )

    data = payload.model_dump()
    yaml_text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

    from src.infra.workspace_domain import set_workspace_domain
    from src.kb_config import reset_workspace_kb_config

    try:
        await set_workspace_domain(workspace_id, data)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Invalidate ONLY this workspace's cache so the next read reflects the new
    # override. Other tenants are untouched.
    reset_workspace_kb_config(workspace_id)

    await _record_config_version(workspace_id, "domain", yaml_text, data)

    record_audit(
        user.id, "config.domain.update",
        target_type="workspace", target_id=workspace_id,
        workspace_id=workspace_id,
        metadata={"domain_name": data.get("domain_name")},
    )
    return {"ok": True, "workspace_id": workspace_id}


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
async def get_graph_cfg(workspace_id: str = Depends(require_workspace_id)):
    # Clear cache so any file edits on disk are reflected.
    get_graph_config.cache_clear()
    gc = get_graph_config()
    return _graph_config_to_payload(gc)


class PutGraphConfigResponse(BaseModel):
    status: str
    path: str
    changed_sections: list[str]
    requires_rebuild: bool


@router.put("/config/graph", response_model=PutGraphConfigResponse, summary="Save graph config snapshot")
async def put_graph_cfg(
    payload: GraphConfigPayload,
    workspace_id: str = Depends(require_workspace_id),
    user: User = Depends(require_user),
):
    if not USE_NEON:
        raise HTTPException(
            status_code=503,
            detail="DATABASE_URL (Neon) is required to persist config changes.",
        )

    # Compute changed sections vs current
    current = _graph_config_to_payload(get_graph_config())
    changed: list[str] = []
    for section in ("embeddings", "edges", "cross_kb", "traversal", "search", "extraction"):
        if getattr(current, section).model_dump() != getattr(payload, section).model_dump():
            changed.append(section)

    data = _payload_to_yaml_dict(payload)
    yaml_text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)

    get_graph_config.cache_clear()

    requires_rebuild = any(s in _REBUILD_SECTIONS for s in changed)
    status = "requires_rebuild" if requires_rebuild else "applied"

    await _record_config_version(
        workspace_id, "graph", yaml_text, data,
        changed_sections=changed,
        requires_rebuild=requires_rebuild,
    )

    record_audit(
        user.id, "config.graph.update",
        target_type="workspace", target_id=workspace_id,
        workspace_id=workspace_id,
        metadata={"changed_sections": changed, "requires_rebuild": requires_rebuild},
    )
    return PutGraphConfigResponse(
        status=status,
        path=f"neon://config_versions/{workspace_id}/graph",
        changed_sections=changed,
        requires_rebuild=requires_rebuild,
    )
