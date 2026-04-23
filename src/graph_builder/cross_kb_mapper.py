"""
Auto-generate cross-KB chapter mappings (Tool KB → Knowledge KB).

Replaces the hardcoded CROSS_KB_SEED_MAPPINGS with a two-signal approach:

  Signal 1 – Embedding similarity
    Embed each chapter's heading + content_summary + raw_text[:500].
    Compute pairwise cosine similarity between every (Tool chapter, Knowledge
    chapter) pair.  Reuses the same TF-IDF / SentenceTransformer model chosen
    by the main embedding pipeline (respects KB_FORCE_TFIDF).

  Signal 2 – Keyword co-occurrence
    For each Tool chapter, collect all ToolNode.tool_name values that live
    under it (via parent_id chain).  For each Knowledge chapter, gather the
    raw_text of every descendant SectionNode.  Count how many of those tool
    names appear in the Knowledge chapter text and normalise to [0, 1].
    This directly measures "which Knowledge chapters mention the tools in this
    Tool chapter" — a strong IMPLEMENTS signal that is orthogonal to embedding.

  Combined score
    score = W_EMBED * cosine_sim + W_COOCCUR * cooccur_norm
    Keep pairs where score >= CROSS_KB_AUTO_THRESHOLD, cap at
    CROSS_KB_MAX_LINKS_PER_CHAPTER per Tool chapter (highest-scoring first).

Output contract
    dict[str, list[str]]  — same format as the old CROSS_KB_SEED_MAPPINGS:
      { "<tool chapter heading>": ["<knowledge chapter heading>", ...] }
    Keys are exact ChapterNode.heading strings so that EdgeBuilder can resolve
    them directly against tool_ch_index / know_ch_index without any changes.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import numpy as np

from src.config import (
    CROSS_KB_AUTO_THRESHOLD,
    CROSS_KB_COOCCUR_WEIGHT,
    CROSS_KB_EMBED_WEIGHT,
    CROSS_KB_MAX_LINKS_PER_CHAPTER,
    EMBEDDING_DIM,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
)
from src.graph_config import get_graph_config
from src.models.nodes import BaseNode, ChapterNode, KBSource, NodeType, SectionNode, ToolNode

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _chapter_text(chapter: ChapterNode) -> str:
    """Build the text blob used for embedding a chapter."""
    parts: list[str] = [chapter.heading]
    if chapter.content_summary and chapter.content_summary != chapter.heading:
        parts.append(chapter.content_summary)
    if chapter.raw_text:
        max_chars = get_graph_config().cross_kb.chapter_embedding_max_chars
        parts.append(chapter.raw_text[:max_chars])
    return " | ".join(parts)


def _load_embedder():
    """
    Return the same embedder as the main embedding pipeline (mirrors _load_model logic):
      1. KB_FORCE_TFIDF=1          → offline TF-IDF fallback
      2. EMBEDDING_MODEL has "/"   → _OpenRouterEmbedder (remote API)
      3. else                      → SentenceTransformer (local)
    """
    force_tfidf = os.environ.get("KB_FORCE_TFIDF", "").lower() in ("1", "true", "yes")
    if force_tfidf:
        logger.info("cross_kb_mapper: using TF-IDF embedder (KB_FORCE_TFIDF set).")
        from src.graph_builder.embeddings import _TFIDFEmbedder
        return _TFIDFEmbedder()

    if "/" in EMBEDDING_MODEL:
        logger.info(f"cross_kb_mapper: using embedding model '{EMBEDDING_MODEL}'…")
        from src.graph_builder.embeddings import _OpenRouterEmbedder
        return _OpenRouterEmbedder(
            EMBEDDING_MODEL, OPENROUTER_API_KEY, OPENROUTER_BASE_URL, EMBEDDING_DIMENSIONS
        )

    logger.info(f"cross_kb_mapper: loading sentence-transformer '{EMBEDDING_MODEL}'…")
    try:
        from sentence_transformers import SentenceTransformer
        return SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)
    except Exception as exc:
        logger.warning(
            f"cross_kb_mapper: SentenceTransformer failed ({exc}). Falling back to TF-IDF."
        )
        from src.graph_builder.embeddings import _TFIDFEmbedder
        return _TFIDFEmbedder()


def _embed_chapters(
    chapters: list[ChapterNode],
    model,
) -> np.ndarray:
    """
    Return a float32 matrix of shape (len(chapters), dim), L2-normalised.
    Returns a zeros matrix if embedding fails entirely (graceful degradation).
    """
    texts = [_chapter_text(ch) for ch in chapters]
    try:
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.array(vecs, dtype=np.float32)
    except Exception as exc:
        logger.warning(f"cross_kb_mapper: embedding failed ({exc}). Signal 1 zeroed out.")
        return np.zeros((len(chapters), EMBEDDING_DIM), dtype=np.float32)


# ---------------------------------------------------------------------------
# Signal 2 – keyword co-occurrence helpers
# ---------------------------------------------------------------------------

def _build_tool_name_index(
    nodes: dict[str, BaseNode],
) -> dict[str, set[str]]:
    """
    Map each Tool KB ChapterNode.node_id → set of normalised tool names
    belonging to ToolNodes whose ancestor chain passes through that chapter.
    """
    # Walk upward from each ToolNode to find its chapter ancestor
    ch_to_tools: dict[str, set[str]] = {}
    min_len = get_graph_config().cross_kb.min_tool_name_length

    for node in nodes.values():
        if not isinstance(node, ToolNode):
            continue
        # Climb parent chain until we hit a ChapterNode
        ancestor = nodes.get(node.parent_id or "")
        while ancestor and not isinstance(ancestor, ChapterNode):
            ancestor = nodes.get(ancestor.parent_id or "")

        if isinstance(ancestor, ChapterNode) and ancestor.kb_source == KBSource.TOOL:
            ch_id = ancestor.node_id
            if ch_id not in ch_to_tools:
                ch_to_tools[ch_id] = set()
            name = (node.tool_name or "").strip().lower()
            if len(name) >= min_len:
                ch_to_tools[ch_id].add(name)

    return ch_to_tools


def _build_knowledge_chapter_text(
    nodes: dict[str, BaseNode],
    knowledge_chapters: list[ChapterNode],
) -> dict[str, str]:
    """
    Map each Knowledge ChapterNode.node_id → concatenated raw_text of all
    descendant SectionNodes (plus the chapter's own raw_text).
    Pre-lowercased for fast substring search.
    """
    # Build parent → children index for quick descendant lookup
    ch_text: dict[str, list[str]] = {ch.node_id: [] for ch in knowledge_chapters}
    ch_ids = {ch.node_id for ch in knowledge_chapters}

    for node in nodes.values():
        if not isinstance(node, SectionNode):
            continue
        if node.kb_source != KBSource.KNOWLEDGE:
            continue
        # Walk up to find the chapter ancestor
        ancestor = nodes.get(node.parent_id or "")
        seen: set[str] = set()
        while ancestor and not isinstance(ancestor, ChapterNode):
            if ancestor.node_id in seen:
                break
            seen.add(ancestor.node_id)
            ancestor = nodes.get(ancestor.parent_id or "")
        if isinstance(ancestor, ChapterNode) and ancestor.node_id in ch_ids:
            ch_text[ancestor.node_id].append((node.raw_text or "").lower())

    # Also include the chapter's own raw_text
    result: dict[str, str] = {}
    for ch in knowledge_chapters:
        parts = [(ch.raw_text or "").lower()] + ch_text.get(ch.node_id, [])
        result[ch.node_id] = " ".join(parts)
    return result


def _cooccurrence_matrix(
    tool_chapters: list[ChapterNode],
    knowledge_chapters: list[ChapterNode],
    ch_to_tools: dict[str, set[str]],
    know_ch_texts: dict[str, str],
) -> np.ndarray:
    """
    Return a float32 matrix (n_tool, n_know) of normalised co-occurrence scores.
    score[i][j] = (# tool names from tool_ch[i] found in know_ch[j]) / max_count_i
    Rows with max_count == 0 are left as zeros.
    """
    n_tool = len(tool_chapters)
    n_know = len(knowledge_chapters)
    matrix = np.zeros((n_tool, n_know), dtype=np.float32)

    for i, tch in enumerate(tool_chapters):
        tool_names = ch_to_tools.get(tch.node_id, set())
        if not tool_names:
            continue
        row = np.zeros(n_know, dtype=np.float32)
        for j, kch in enumerate(knowledge_chapters):
            ktext = know_ch_texts.get(kch.node_id, "")
            count = sum(1 for name in tool_names if name in ktext)
            row[j] = float(count)
        row_max = row.max()
        if row_max > 0:
            matrix[i] = row / row_max

    return matrix


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_cross_kb_mappings(
    nodes: dict[str, BaseNode],
) -> dict[str, list[str]]:
    """
    Dynamically generate Tool KB → Knowledge KB chapter mappings.

    Args:
        nodes: The full node dict produced by the extractor.

    Returns:
        dict mapping each Tool chapter heading to a list of Knowledge chapter
        headings, using the same string format as the old CROSS_KB_SEED_MAPPINGS.
    """
    # -- Partition ChapterNodes by KB source, skip TOC --
    skip_headings = set(get_graph_config().cross_kb.skip_chapter_headings)
    tool_chapters: list[ChapterNode] = []
    know_chapters: list[ChapterNode] = []

    for node in nodes.values():
        if not isinstance(node, ChapterNode):
            continue
        if node.heading in skip_headings:
            continue
        if node.kb_source == KBSource.TOOL:
            tool_chapters.append(node)
        elif node.kb_source == KBSource.KNOWLEDGE:
            know_chapters.append(node)

    if not tool_chapters or not know_chapters:
        logger.warning("cross_kb_mapper: no chapters found; returning empty mapping.")
        return {}

    logger.info(
        f"cross_kb_mapper: {len(tool_chapters)} tool chapters × "
        f"{len(know_chapters)} knowledge chapters"
    )

    # -----------------------------------------------------------------------
    # Signal 1: embedding cosine similarity
    # -----------------------------------------------------------------------
    try:
        model = _load_embedder()
        tool_vecs = _embed_chapters(tool_chapters, model)
        know_vecs = _embed_chapters(know_chapters, model)
        # cosine similarity matrix (vectors already L2-normalised → dot product)
        sim_matrix = tool_vecs @ know_vecs.T   # shape (n_tool, n_know)
        # Clip negatives (cosine can be negative with TF-IDF)
        sim_matrix = np.clip(sim_matrix, 0.0, 1.0)
    except Exception as exc:
        logger.warning(f"cross_kb_mapper: Signal 1 failed entirely ({exc}). Zeroing out.")
        sim_matrix = np.zeros((len(tool_chapters), len(know_chapters)), dtype=np.float32)

    # -----------------------------------------------------------------------
    # Signal 2: keyword co-occurrence
    # -----------------------------------------------------------------------
    ch_to_tools = _build_tool_name_index(nodes)
    know_ch_texts = _build_knowledge_chapter_text(nodes, know_chapters)
    cooccur_matrix = _cooccurrence_matrix(
        tool_chapters, know_chapters, ch_to_tools, know_ch_texts
    )

    # -----------------------------------------------------------------------
    # Combine signals
    # -----------------------------------------------------------------------
    score_matrix = (
        CROSS_KB_EMBED_WEIGHT * sim_matrix
        + CROSS_KB_COOCCUR_WEIGHT * cooccur_matrix
    )

    # -----------------------------------------------------------------------
    # Threshold and build output dict
    # -----------------------------------------------------------------------
    mappings: dict[str, list[str]] = {}

    for i, tch in enumerate(tool_chapters):
        scores = score_matrix[i]  # shape (n_know,)
        # Collect candidates above threshold
        candidates = [
            (float(scores[j]), know_chapters[j].heading)
            for j in range(len(know_chapters))
            if float(scores[j]) >= CROSS_KB_AUTO_THRESHOLD
        ]
        # Sort descending by score, cap to max links
        candidates.sort(key=lambda x: x[0], reverse=True)
        top = candidates[:CROSS_KB_MAX_LINKS_PER_CHAPTER]

        if top:
            mappings[tch.heading] = [heading for _, heading in top]
            logger.debug(
                f"  {tch.heading!r} → "
                + ", ".join(f"{h!r}({s:.2f})" for s, h in candidates[:CROSS_KB_MAX_LINKS_PER_CHAPTER])
            )

    covered = len(mappings)
    logger.info(
        f"cross_kb_mapper: {covered}/{len(tool_chapters)} tool chapters mapped "
        f"(threshold={CROSS_KB_AUTO_THRESHOLD}, "
        f"embed_w={CROSS_KB_EMBED_WEIGHT}, cooccur_w={CROSS_KB_COOCCUR_WEIGHT})"
    )

    return mappings
