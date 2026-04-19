"""
Node extractor: converts ParsedKB objects into typed graph node objects.

Extraction rules:
  - Each chapter   -> ChapterNode
  - Each section   -> SectionNode (recursively, any depth)
  - Tables in Tool KB with "tool-like" column headers -> ToolNode per row
  - Tables in Tool KB with "process-like" column headers -> ProcessNode per step row
  - Tables in Knowledge KB Chapter 24 with Term/Definition headers -> GlossaryNode per row
  - All tables (both KBs) -> TableNode (metadata) + row nodes children
  - One root DomainNode per KB
"""

from __future__ import annotations

import re
from typing import Any

from src.graph_builder.parser import ContentBlock, ParsedChapter, ParsedKB, ParsedSection
from src.graph_config import get_graph_config
from src.kb_config import get_active_kb_config
from src.models.nodes import (
    BaseNode,
    ChapterNode,
    ConceptNode,
    DomainNode,
    EdgeType,
    GlossaryNode,
    KBSource,
    NodeType,
    ProcessNode,
    SectionNode,
    TableNode,
    ToolNode,
)


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

def _slug(text: str, max_len: int | None = None) -> str:
    if max_len is None:
        max_len = get_graph_config().extraction.max_slug_length
    text = text.lower()
    text = re.sub(r"[^a-z0-9]", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text[:max_len]


def _chapter_id(kb: str, ch_num: int) -> str:
    return f"{kb}:ch{ch_num}"


def _section_id(kb: str, ch_num: int, outline_path: str, heading: str) -> str:
    return f"{kb}:ch{ch_num}:s{outline_path}-{_slug(heading)}"


def _table_id(kb: str, ch_num: int, sec_id: str, table_idx: int) -> str:
    return f"{sec_id}:tbl{table_idx}"


def _tool_node_id(tbl_id: str, row_idx: int) -> str:
    return f"{tbl_id}:tool{row_idx}"


def _process_node_id(tbl_id: str, row_idx: int) -> str:
    return f"{tbl_id}:step{row_idx}"


def _glossary_id(kb: str, term: str) -> str:
    return f"{kb}:glossary:{_slug(term)}"


# ---------------------------------------------------------------------------
# Column classifier helpers
# ---------------------------------------------------------------------------

def _primary_header(headers: list[str]) -> str | None:
    """Return the first header that looks like a tool identifier."""
    kws = get_active_kb_config().tool_column_keywords
    for h in headers:
        if any(kw.lower() in h.lower() for kw in kws):
            return h
    return None


def _is_tool_table(headers: list[str]) -> bool:
    return _primary_header(headers) is not None


def _primary_process_header(headers: list[str]) -> str | None:
    kws = get_active_kb_config().process_column_keywords
    for h in headers:
        if any(kw.lower() in h.lower() for kw in kws):
            return h
    return None


def _is_process_table(headers: list[str]) -> bool:
    return _primary_process_header(headers) is not None


def _is_glossary_table(headers: list[str]) -> bool:
    lower = [h.lower() for h in headers]
    return "term" in lower and "definition" in lower


# ---------------------------------------------------------------------------
# Row -> ToolNode
# ---------------------------------------------------------------------------

def _row_to_tool_node(
    row: dict[str, str],
    node_id: str,
    parent_id: str,
    kb_source: KBSource,
    primary_col: str,
) -> ToolNode:
    tool_name = row.get(primary_col, "")

    # Provider detection
    provider_keys = [k for k in row if "provider" in k.lower() or "vendor" in k.lower()]
    provider = row.get(provider_keys[0], "") if provider_keys else ""

    # Purpose detection
    purpose_keys = [k for k in row if "purpose" in k.lower() or "function" in k.lower()
                    or "description" in k.lower() or "role" in k.lower()]
    purpose = row.get(purpose_keys[0], "") if purpose_keys else ""

    # Connected systems
    conn_keys = [k for k in row if "connect" in k.lower() or "integration" in k.lower()
                 or "integrated" in k.lower()]
    conn_raw = row.get(conn_keys[0], "") if conn_keys else ""
    connected_systems = [s.strip() for s in re.split(r"[,;/]", conn_raw) if s.strip()]

    # SLA
    sla_keys = [k for k in row if "sla" in k.lower() or "uptime" in k.lower()]
    sla = row.get(sla_keys[0], "") if sla_keys else ""

    # Data classification
    dc_keys = [k for k in row if "classification" in k.lower() or "data class" in k.lower()]
    data_cls = row.get(dc_keys[0], "") if dc_keys else ""

    # Contract term
    ct_keys = [k for k in row if "contract" in k.lower() or "term" in k.lower()]
    contract_term = row.get(ct_keys[0], "") if ct_keys else ""

    # Category (sometimes a column)
    cat_keys = [k for k in row if "category" in k.lower() or "type" in k.lower()]
    category = row.get(cat_keys[0], "") if cat_keys else ""

    raw_text = "; ".join(f"{k}: {v}" for k, v in row.items() if v)
    summary_max = get_graph_config().extraction.toolnode_summary_max_chars

    return ToolNode(
        node_id=node_id,
        kb_source=kb_source,
        heading=tool_name or node_id,
        tool_name=tool_name,
        provider=provider,
        purpose=purpose,
        category=category,
        connected_systems=connected_systems,
        sla=sla,
        data_classification=data_cls,
        contract_term=contract_term,
        raw_row=dict(row),
        raw_text=raw_text,
        content_summary=purpose[:summary_max] if purpose else raw_text[:summary_max],
        parent_id=parent_id,
    )


# ---------------------------------------------------------------------------
# Row -> ProcessNode
# ---------------------------------------------------------------------------

def _row_to_process_node(
    row: dict[str, str],
    node_id: str,
    parent_id: str,
    kb_source: KBSource,
    step_col: str,
    step_number: int,
) -> ProcessNode:
    step_name = row.get(step_col, f"Step {step_number}")

    system_keys = [k for k in row if "system" in k.lower() or "tool" in k.lower()
                   or "platform" in k.lower()]
    system_used = row.get(system_keys[0], "") if system_keys else ""

    action_keys = [k for k in row if "action" in k.lower() or "required" in k.lower()
                   or "task" in k.lower() or "description" in k.lower()]
    required_actions = row.get(action_keys[0], "") if action_keys else ""

    time_keys = [k for k in row if "time" in k.lower() or "duration" in k.lower()
                 or "hours" in k.lower()]
    time_to_complete = row.get(time_keys[0], "") if time_keys else ""

    comp_keys = [k for k in row if "compliance" in k.lower() or "check" in k.lower()
                 or "verify" in k.lower()]
    compliance_checks = row.get(comp_keys[0], "") if comp_keys else ""

    raw_text = "; ".join(f"{k}: {v}" for k, v in row.items() if v)
    summary_max = get_graph_config().extraction.processnode_summary_max_chars

    return ProcessNode(
        node_id=node_id,
        kb_source=kb_source,
        heading=step_name,
        step_name=step_name,
        step_number=step_number,
        system_used=system_used,
        required_actions=required_actions,
        time_to_complete=time_to_complete,
        compliance_checks=compliance_checks,
        raw_row=dict(row),
        raw_text=raw_text,
        content_summary=f"{step_name}: {required_actions}"[:summary_max],
        parent_id=parent_id,
    )


# ---------------------------------------------------------------------------
# Table block -> TableNode + row nodes
# ---------------------------------------------------------------------------

def _extract_table_nodes(
    block: ContentBlock,
    table_id: str,
    section_id: str,
    kb_source: KBSource,
) -> tuple[TableNode, list[BaseNode]]:
    """Return a TableNode and list of ToolNode/ProcessNode/GlossaryNode children."""
    headers = block.headers
    rows = block.rows
    caption = block.caption

    max_headers = get_graph_config().extraction.tablenode_summary_max_headers
    table_node = TableNode(
        node_id=table_id,
        kb_source=kb_source,
        heading=caption or f"Table {table_id}",
        caption=caption,
        headers=headers,
        row_count=len(rows),
        content_summary=f"{caption or 'Table'} with columns: {', '.join(headers[:max_headers])}",
        raw_text=caption,
        parent_id=section_id,
    )

    row_nodes: list[BaseNode] = []

    if _is_glossary_table(headers):
        # Build GlossaryNode or ConceptNode
        term_col = next((h for h in headers if h.lower() == "term"), headers[0])
        def_col = next((h for h in headers if h.lower() == "definition"), headers[-1])
        for row_idx, row in enumerate(rows):
            term = row.get(term_col, "")
            definition = row.get(def_col, "")
            if not term:
                continue
            gloss_max = get_graph_config().extraction.glossarynode_summary_max_chars
            gn = GlossaryNode(
                node_id=_glossary_id(kb_source.value, term),
                kb_source=kb_source,
                heading=term,
                term=term,
                definition=definition,
                raw_text=f"{term}: {definition}",
                content_summary=definition[:gloss_max],
                parent_id=table_id,
            )
            row_nodes.append(gn)
            table_node.row_node_ids.append(gn.node_id)

    elif _is_process_table(headers):
        step_col = _primary_process_header(headers) or headers[0]
        for row_idx, row in enumerate(rows):
            nid = _process_node_id(table_id, row_idx)
            pn = _row_to_process_node(row, nid, table_id, kb_source, step_col, row_idx + 1)
            row_nodes.append(pn)
            table_node.row_node_ids.append(nid)

    elif _is_tool_table(headers) and kb_source == KBSource.TOOL:
        primary_col = _primary_header(headers) or headers[0]
        for row_idx, row in enumerate(rows):
            nid = _tool_node_id(table_id, row_idx)
            tn = _row_to_tool_node(row, nid, table_id, kb_source, primary_col)
            if tn.tool_name:
                row_nodes.append(tn)
                table_node.row_node_ids.append(nid)

    return table_node, row_nodes


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------

class NodeExtractor:
    """
    Walks a ParsedKB and emits all node objects with stable IDs.
    Results are collected in self.nodes (dict[node_id, BaseNode]).
    """

    def __init__(self, kb: ParsedKB) -> None:
        self.kb = kb
        self.kb_source = KBSource(kb.kb_source)
        self.nodes: dict[str, BaseNode] = {}

    # ------------------------------------------------------------------
    def extract(self) -> dict[str, BaseNode]:
        self._add_domain_root()
        for ch in self.kb.chapters:
            self._extract_chapter(ch)
        return self.nodes

    # ------------------------------------------------------------------
    def _add(self, node: BaseNode) -> None:
        self.nodes[node.node_id] = node

    def _add_domain_root(self) -> None:
        root_id = f"{self.kb_source.value}:root"
        root = DomainNode(
            node_id=root_id,
            kb_source=self.kb_source,
            heading=self.kb.title,
            content_summary=self.kb.subtitle,
            raw_text=self.kb.subtitle,
        )
        self._add(root)

    # ------------------------------------------------------------------
    def _extract_chapter(self, ch: ParsedChapter) -> None:
        ch_id = _chapter_id(self.kb_source.value, ch.chapter_num)
        root_id = f"{self.kb_source.value}:root"

        ch_node = ChapterNode(
            node_id=ch_id,
            kb_source=self.kb_source,
            heading=ch.heading,
            chapter_num=ch.chapter_num,
            level=ch.level,
            parent_id=root_id,
            content_summary=ch.heading,
            raw_text="\n".join(b.plain_text() for b in ch.content_blocks),
        )
        self._add(ch_node)

        for sec_idx, sec in enumerate(ch.sections):
            self._extract_section(sec, ch_id, ch.chapter_num)

    # ------------------------------------------------------------------
    def _extract_section(
        self,
        sec: ParsedSection,
        parent_id: str,
        chapter_num: int,
    ) -> SectionNode:
        sec_id = _section_id(
            self.kb_source.value,
            chapter_num,
            sec.outline_path,
            sec.heading,
        )

        ex_cfg = get_graph_config().extraction
        paragraphs = sec.all_paragraphs()
        summary = sec.summary(ex_cfg.section_summary_max_chars)

        sec_node = SectionNode(
            node_id=sec_id,
            kb_source=self.kb_source,
            heading=sec.heading,
            level=sec.level,
            outline_path=sec.outline_path,
            parent_id=parent_id,
            paragraphs=paragraphs[: ex_cfg.max_paragraphs_per_section],
            content_summary=summary,
            raw_text=sec.all_text(),
        )
        self._add(sec_node)

        # Extract table nodes
        for tbl_idx, block in enumerate(sec.all_tables()):
            tbl_id = _table_id(self.kb_source.value, chapter_num, sec_id, tbl_idx)
            tbl_node, row_nodes = _extract_table_nodes(block, tbl_id, sec_id, self.kb_source)
            self._add(tbl_node)
            sec_node.table_ids.append(tbl_id)
            for rn in row_nodes:
                self._add(rn)

        # Recurse into subsections
        for child_sec in sec.children:
            child_node = self._extract_section(child_sec, sec_id, chapter_num)
            sec_node.child_section_ids.append(child_node.node_id)

        return sec_node


def extract_all_nodes(
    knowledge_kb: ParsedKB,
    tool_kb: ParsedKB,
    workspace_id: str | None = None,
) -> dict[str, BaseNode]:
    """Top-level entry point: extract nodes from both KBs and stamp workspace_id."""
    nodes: dict[str, BaseNode] = {}
    for kb in (knowledge_kb, tool_kb):
        extractor = NodeExtractor(kb)
        nodes.update(extractor.extract())
    if workspace_id:
        for n in nodes.values():
            n.workspace_id = workspace_id
    return nodes
