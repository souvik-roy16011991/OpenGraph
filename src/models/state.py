"""
LangGraph agent state schema.
"""

from __future__ import annotations

from typing import Any, Optional
from typing_extensions import TypedDict


class StepDetail(TypedDict):
    step_number: int
    title: str
    description: str
    node_id: str
    node_type: str
    tools: list[dict[str, Any]]
    related_concepts: list[str]


class GraphAgentState(TypedDict):
    # Input
    query: str

    # Classification
    intent: str                          # "explore" | "process" | "tool_lookup" | "compare"
    extracted_topics: list[str]          # key topics/entities from query
    kb_focus: str                        # "knowledge" | "tool" | "both"

    # Graph traversal
    entry_nodes: list[str]               # starting node IDs
    traversal_path: list[str]            # ordered node IDs visited
    visited_node_ids: list[str]          # all visited (dedup)
    gathered_context: list[dict[str, Any]]  # content from visited nodes

    # Output construction
    steps: list[StepDetail]              # ordered step-by-step output
    tools_referenced: list[dict[str, Any]]  # tool details with full schemas
    knowledge_concepts: list[dict[str, Any]]  # knowledge terms referenced

    # Final response
    response: str
    follow_up_suggestions: list[str]

    # Control
    traversal_depth: int
    needs_more_context: bool
    error: Optional[str]

    # LLM selection (resolved by the caller: request override > workspace
    # default > env LLM_MODEL). When None, nodes fall back to env default.
    llm_model: Optional[str]
