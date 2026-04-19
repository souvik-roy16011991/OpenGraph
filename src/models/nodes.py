"""
Pydantic models for all graph node types and edge definitions.

Node ID convention:
  knowledge:ch{N}                            ChapterNode  (Knowledge KB)
  knowledge:ch{N}:s{heading_slug}            SectionNode
  knowledge:ch{N}:s{slug}:ss{slug}           SectionNode (subsection)
  tool:ch{N}                                 ChapterNode  (Tool KB)
  tool:ch{N}:s{slug}                         SectionNode
  tool:ch{N}:s{slug}:t{row_idx}              ToolNode extracted from table
  tool:ch{N}:s{slug}:p{row_idx}              ProcessNode step
  knowledge:glossary:{term_slug}             GlossaryNode
  knowledge:ch24:concept:{term_slug}         ConceptNode
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Union
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class KBSource(str, Enum):
    KNOWLEDGE = "knowledge"
    TOOL = "tool"
    DERIVED = "derived"


class NodeType(str, Enum):
    DOMAIN = "domain"
    CHAPTER = "chapter"
    SECTION = "section"
    CONCEPT = "concept"
    TOOL = "tool"
    PROCESS = "process"
    TABLE = "table"
    GLOSSARY = "glossary"


class EdgeType(str, Enum):
    CONTAINS = "CONTAINS"               # hierarchical parent -> child
    HAS_CONTENT = "HAS_CONTENT"         # section -> table / concept
    NEXT_STEP = "NEXT_STEP"             # ordered steps within a process
    USES_TOOL = "USES_TOOL"             # knowledge concept / process -> tool
    INTEGRATES_WITH = "INTEGRATES_WITH" # tool -> tool (Connected Systems)
    IMPLEMENTS = "IMPLEMENTS"           # tool -> knowledge domain it supports
    RELATED_TO = "RELATED_TO"           # semantic similarity
    DEFINED_IN = "DEFINED_IN"           # glossary term -> sections


# ---------------------------------------------------------------------------
# Base node
# ---------------------------------------------------------------------------

class BaseNode(BaseModel):
    node_id: str
    node_type: NodeType
    kb_source: KBSource
    heading: str
    level: int = 0
    parent_id: Optional[str] = None
    content_summary: str = ""
    raw_text: str = ""
    embedding: Optional[list[float]] = Field(default=None, exclude=True)

    def label(self) -> str:
        return self.heading

    def to_dict(self) -> dict[str, Any]:
        d = self.model_dump(exclude={"embedding"})
        d["label"] = self.label()
        return d


# ---------------------------------------------------------------------------
# Concrete node types
# ---------------------------------------------------------------------------

class DomainNode(BaseNode):
    """Top-level domain grouping node derived at build time."""
    node_type: NodeType = NodeType.DOMAIN
    kb_source: KBSource = KBSource.DERIVED
    level: int = 0
    domains: list[str] = Field(default_factory=list)


class ChapterNode(BaseNode):
    """Represents a top-level chapter in either KB."""
    node_type: NodeType = NodeType.CHAPTER
    chapter_num: int = 0
    section_ids: list[str] = Field(default_factory=list)


class SectionNode(BaseNode):
    """Represents a section or subsection (any depth)."""
    node_type: NodeType = NodeType.SECTION
    outline_path: str = ""         # e.g. "3.2.1"
    paragraphs: list[str] = Field(default_factory=list)
    table_ids: list[str] = Field(default_factory=list)
    child_section_ids: list[str] = Field(default_factory=list)


class ToolNode(BaseNode):
    """Represents a single tool/system extracted from a table row."""
    node_type: NodeType = NodeType.TOOL
    level: int = 3
    tool_name: str = ""
    provider: str = ""
    purpose: str = ""
    category: str = ""
    connected_systems: list[str] = Field(default_factory=list)
    config_details: dict[str, str] = Field(default_factory=dict)
    sla: str = ""
    data_classification: str = ""
    contract_term: str = ""
    # Full original table row stored for "schema" display
    raw_row: dict[str, str] = Field(default_factory=dict)

    def label(self) -> str:
        return self.tool_name or self.heading


class ProcessNode(BaseNode):
    """Represents a workflow step extracted from a workflow/process table."""
    node_type: NodeType = NodeType.PROCESS
    level: int = 3
    step_name: str = ""
    step_number: int = 0
    system_used: str = ""
    required_actions: str = ""
    time_to_complete: str = ""
    compliance_checks: str = ""
    raw_row: dict[str, str] = Field(default_factory=dict)

    def label(self) -> str:
        prefix = f"Step {self.step_number}: " if self.step_number else ""
        return prefix + (self.step_name or self.heading)


class TableNode(BaseNode):
    """Represents an entire table (metadata level)."""
    node_type: NodeType = NodeType.TABLE
    level: int = 3
    caption: str = ""
    headers: list[str] = Field(default_factory=list)
    row_count: int = 0
    row_node_ids: list[str] = Field(default_factory=list)

    def label(self) -> str:
        return self.caption or self.heading


class GlossaryNode(BaseNode):
    """Glossary term from Knowledge KB Chapter 24."""
    node_type: NodeType = NodeType.GLOSSARY
    level: int = 3
    term: str = ""
    definition: str = ""

    def label(self) -> str:
        return self.term


class ConceptNode(BaseNode):
    """Important concept / term extracted from knowledge KB paragraphs."""
    node_type: NodeType = NodeType.CONCEPT
    level: int = 3
    term: str = ""
    definition: str = ""
    context_text: str = ""

    def label(self) -> str:
        return self.term


# ---------------------------------------------------------------------------
# Edge model
# ---------------------------------------------------------------------------

class Edge(BaseModel):
    source_id: str
    target_id: str
    edge_type: EdgeType
    weight: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# ---------------------------------------------------------------------------
# Union type alias for type checking
# ---------------------------------------------------------------------------

AnyNode = Union[
    DomainNode,
    ChapterNode,
    SectionNode,
    ToolNode,
    ProcessNode,
    TableNode,
    GlossaryNode,
    ConceptNode,
]


def node_from_dict(data: dict[str, Any]) -> BaseNode:
    """Deserialise a node dict back to the correct Pydantic type."""
    type_map = {
        NodeType.DOMAIN: DomainNode,
        NodeType.CHAPTER: ChapterNode,
        NodeType.SECTION: SectionNode,
        NodeType.TOOL: ToolNode,
        NodeType.PROCESS: ProcessNode,
        NodeType.TABLE: TableNode,
        NodeType.GLOSSARY: GlossaryNode,
        NodeType.CONCEPT: ConceptNode,
    }
    nt = NodeType(data["node_type"])
    cls = type_map.get(nt, BaseNode)
    return cls(**data)
