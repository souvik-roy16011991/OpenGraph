"""
Edge construction for the Knowledge Graph.

Edge types built here:
  CONTAINS        – hierarchical parent->child (chapter->section->subsection->table->row)
  HAS_CONTENT     – section -> table node
  NEXT_STEP       – sequential ProcessNode ordering within a table
  INTEGRATES_WITH – ToolNode -> ToolNode inferred from "Connected Systems" column values
  IMPLEMENTS      – ToolNode chapter -> KnowledgeKB chapter (seed + LLM-refined)
  USES_TOOL       – SectionNode (knowledge) -> ToolNode where tool name appears in text
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from src.config import CROSS_LINKS_PATH
from src.graph_config import get_graph_config
from src.models.nodes import (
    BaseNode,
    ChapterNode,
    Edge,
    EdgeType,
    GlossaryNode,
    KBSource,
    NodeType,
    ProcessNode,
    SectionNode,
    TableNode,
    ToolNode,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _build_tool_name_index(nodes: dict[str, BaseNode]) -> dict[str, str]:
    """Build a normalised-name -> node_id index for all ToolNodes."""
    aliases_enabled = get_graph_config().edges.enable_tool_name_aliases
    idx: dict[str, str] = {}
    for nid, node in nodes.items():
        if isinstance(node, ToolNode) and node.tool_name:
            idx[_normalise_name(node.tool_name)] = nid
            if aliases_enabled:
                words = node.tool_name.split()
                if len(words) > 1:
                    idx[_normalise_name(words[0])] = nid
    return idx


def _resolve_tool_name(name: str, tool_index: dict[str, str]) -> str | None:
    key = _normalise_name(name)
    if key in tool_index:
        return tool_index[key]
    # Partial match: check if any tool index key starts with the key token
    for idx_key, nid in tool_index.items():
        if key and len(key) >= 4 and (idx_key.startswith(key) or key in idx_key):
            return nid
    return None


# ---------------------------------------------------------------------------
# Edge builders
# ---------------------------------------------------------------------------

class EdgeBuilder:
    """
    Constructs all edges from the node registry.
    Returns a list of Edge objects ready to be added to the NetworkX graph.
    """

    def __init__(self, nodes: dict[str, BaseNode]) -> None:
        self.nodes = nodes
        self.edges: list[Edge] = []
        self._tool_name_index = _build_tool_name_index(nodes)
        self._cfg = get_graph_config().edges

    # ------------------------------------------------------------------
    def build_all(self, use_llm_cross_links: bool = True) -> list[Edge]:
        logger.info("Building hierarchical CONTAINS edges…")
        self._build_contains_edges()

        logger.info("Building HAS_CONTENT edges (section -> table)…")
        self._build_has_content_edges()

        logger.info("Building NEXT_STEP edges for ProcessNodes…")
        self._build_next_step_edges()

        logger.info("Building INTEGRATES_WITH edges from Connected Systems…")
        self._build_integrates_with_edges()

        logger.info("Building IMPLEMENTS edges (Tool KB -> Knowledge KB)…")
        self._build_implements_edges(use_llm=use_llm_cross_links)

        logger.info("Building USES_TOOL edges (Knowledge sections -> Tools)…")
        self._build_uses_tool_edges()

        logger.info(f"Total edges constructed: {len(self.edges)}")
        return self.edges

    # ------------------------------------------------------------------
    # 1. CONTAINS – hierarchical
    # ------------------------------------------------------------------
    def _build_contains_edges(self) -> None:
        w = self._cfg.weights.contains
        for nid, node in self.nodes.items():
            if node.parent_id and node.parent_id in self.nodes:
                self._add(node.parent_id, nid, EdgeType.CONTAINS, weight=w)

    # ------------------------------------------------------------------
    # 2. HAS_CONTENT – section -> table
    # ------------------------------------------------------------------
    def _build_has_content_edges(self) -> None:
        for nid, node in self.nodes.items():
            if isinstance(node, SectionNode):
                for tbl_id in node.table_ids:
                    if tbl_id in self.nodes:
                        self._add(nid, tbl_id, EdgeType.HAS_CONTENT)

    # ------------------------------------------------------------------
    # 3. NEXT_STEP – sequential ProcessNodes within same table
    # ------------------------------------------------------------------
    def _build_next_step_edges(self) -> None:
        # Group ProcessNodes by parent table
        table_steps: dict[str, list[ProcessNode]] = {}
        for node in self.nodes.values():
            if isinstance(node, ProcessNode) and node.parent_id:
                table_steps.setdefault(node.parent_id, []).append(node)

        w = self._cfg.weights.next_step
        for tbl_id, steps in table_steps.items():
            # Sort by step number
            steps_sorted = sorted(steps, key=lambda s: s.step_number)
            for i in range(len(steps_sorted) - 1):
                self._add(
                    steps_sorted[i].node_id,
                    steps_sorted[i + 1].node_id,
                    EdgeType.NEXT_STEP,
                    weight=w,
                )

    # ------------------------------------------------------------------
    # 4. INTEGRATES_WITH – tool -> tool from Connected Systems
    # ------------------------------------------------------------------
    def _build_integrates_with_edges(self) -> None:
        w = self._cfg.weights.integrates_with
        for node in self.nodes.values():
            if not isinstance(node, ToolNode):
                continue
            for sys_name in node.connected_systems:
                target_id = _resolve_tool_name(sys_name, self._tool_name_index)
                if target_id and target_id != node.node_id:
                    self._add(
                        node.node_id,
                        target_id,
                        EdgeType.INTEGRATES_WITH,
                        weight=w,
                        metadata={"source_text": sys_name},
                    )

    # ------------------------------------------------------------------
    # 5. IMPLEMENTS – Tool KB chapter -> Knowledge KB chapter
    # ------------------------------------------------------------------
    def _build_implements_edges(self, use_llm: bool = True) -> None:
        # Load cached cross-links if present
        cross_links = self._load_cross_links()

        if not cross_links:
            cross_links = self._generate_seed_cross_links()
            if use_llm:
                try:
                    cross_links = self._refine_with_llm(cross_links)
                except Exception as exc:
                    logger.warning(f"LLM cross-link refinement failed: {exc}")
            self._save_cross_links(cross_links)

        tool_ch_index = {
            node.heading: node.node_id
            for node in self.nodes.values()
            if isinstance(node, ChapterNode) and node.kb_source == KBSource.TOOL
        }
        know_ch_index = {
            node.heading: node.node_id
            for node in self.nodes.values()
            if isinstance(node, ChapterNode) and node.kb_source == KBSource.KNOWLEDGE
        }

        w = self._cfg.weights.implements
        for tool_heading, know_headings in cross_links.items():
            tool_id = tool_ch_index.get(tool_heading)
            if not tool_id:
                continue
            for kh in know_headings:
                know_id = know_ch_index.get(kh)
                if know_id:
                    self._add(tool_id, know_id, EdgeType.IMPLEMENTS, weight=w,
                              metadata={"cross_kb": True})

    def _generate_seed_cross_links(self) -> dict[str, list[str]]:
        """Auto-generate cross-KB chapter mappings using embedding + co-occurrence signals."""
        from src.graph_builder.cross_kb_mapper import generate_cross_kb_mappings
        return generate_cross_kb_mappings(self.nodes)

    def _refine_with_llm(self, seed: dict[str, list[str]]) -> dict[str, list[str]]:
        """
        Ask the LLM to suggest additional cross-links between Tool KB and Knowledge KB.
        This is called once at build time and cached.
        """
        from langchain_openai import ChatOpenAI
        from src.config import LLM_MODEL, LLM_TEMPERATURE, OPENROUTER_API_KEY, OPENROUTER_BASE_URL

        tool_headings = [
            node.heading
            for node in self.nodes.values()
            if isinstance(node, ChapterNode) and node.kb_source == KBSource.TOOL
        ]
        know_headings = [
            node.heading
            for node in self.nodes.values()
            if isinstance(node, ChapterNode) and node.kb_source == KBSource.KNOWLEDGE
        ]

        from src.kb_config import get_active_kb_config
        domain_label = get_active_kb_config().profile.domain_display_name

        prompt = (
            "You are building a knowledge graph that links a Tools/Systems KB and a "
            f"{domain_label} domain Knowledge KB.\n\n"
            f"Tool KB chapters:\n{json.dumps(tool_headings, indent=2)}\n\n"
            f"Knowledge KB chapters:\n{json.dumps(know_headings, indent=2)}\n\n"
            "For each Tool KB chapter, return a JSON object mapping each tool chapter heading "
            "to a list of Knowledge KB chapter headings it IMPLEMENTS or directly supports. "
            "Only include strong relationships. Return ONLY valid JSON, no markdown.\n"
            "Example format:\n"
            '{"Chapter N: Tool Chapter Name": ["Chapter M: Knowledge Chapter Name"]}'
        )

        llm = ChatOpenAI(
            model=LLM_MODEL,
            openai_api_base=OPENROUTER_BASE_URL,
            openai_api_key=OPENROUTER_API_KEY,
            temperature=LLM_TEMPERATURE,
            max_tokens=get_graph_config().cross_kb.llm_max_tokens,
        )
        result = llm.invoke(prompt)
        text = result.content if hasattr(result, "content") else str(result)

        # Extract JSON
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            try:
                llm_links: dict[str, list[str]] = json.loads(json_match.group())
                # Merge with seeds
                for k, v in llm_links.items():
                    if k in seed:
                        existing = set(seed[k])
                        existing.update(v)
                        seed[k] = list(existing)
                    else:
                        seed[k] = v
            except json.JSONDecodeError as exc:
                logger.warning(f"Could not parse LLM cross-link JSON: {exc}")
        return seed

    # ------------------------------------------------------------------
    # 6. USES_TOOL – Knowledge sections that mention a tool name -> ToolNode
    # ------------------------------------------------------------------
    def _build_uses_tool_edges(self) -> None:
        min_len = self._cfg.min_tool_mention_length
        w = self._cfg.weights.uses_tool
        for node in self.nodes.values():
            if not isinstance(node, SectionNode):
                continue
            if node.kb_source != KBSource.KNOWLEDGE:
                continue
            text = (node.raw_text or "").lower()
            for tool_key, tool_id in self._tool_name_index.items():
                if len(tool_key) >= min_len and tool_key in text:
                    self._add(
                        node.node_id,
                        tool_id,
                        EdgeType.USES_TOOL,
                        weight=w,
                        metadata={"mention": tool_key},
                    )

    # ------------------------------------------------------------------
    # Persistence helpers for LLM cross-links
    # ------------------------------------------------------------------
    def _load_cross_links(self) -> dict[str, list[str]]:
        path = Path(CROSS_LINKS_PATH)
        if path.exists():
            try:
                with open(path) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_cross_links(self, data: dict[str, list[str]]) -> None:
        path = Path(CROSS_LINKS_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    # ------------------------------------------------------------------
    def _add(
        self,
        source_id: str,
        target_id: str,
        edge_type: EdgeType,
        weight: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.edges.append(
            Edge(
                source_id=source_id,
                target_id=target_id,
                edge_type=edge_type,
                weight=weight,
                metadata=metadata or {},
            )
        )


def build_edges(
    nodes: dict[str, BaseNode],
    use_llm_cross_links: bool = True,
) -> list[Edge]:
    builder = EdgeBuilder(nodes)
    return builder.build_all(use_llm_cross_links=use_llm_cross_links)
