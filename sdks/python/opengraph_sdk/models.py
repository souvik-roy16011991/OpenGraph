"""Typed response models for /api/v1/ext/*.

Mirrors :mod:`src.api.ext_models` on the server side one-for-one. These
are hand-written (not codegen'd) so we can add properties + docstrings
for a cleaner DX — but the field set matches the wire format exactly so
they can be swapped out for codegen output later without a breaking change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    # Allow extra fields from the server so a future field-addition in v1
    # doesn't break older SDK versions.
    model_config = ConfigDict(extra="allow")


class ChatUsage(_Base):
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_total_tokens: int = 0
    llm_calls: int = 0
    model: Optional[str] = None


class QueryResponse(_Base):
    session_id: str
    response: str
    intent: str = ""
    kb_focus: str = "both"
    extracted_topics: list[str] = Field(default_factory=list)
    follow_up_suggestions: list[str] = Field(default_factory=list)
    llm_model: Optional[str] = None
    usage: Optional[ChatUsage] = None
    duration_ms: int = 0
    history_persisted: bool = True

    # Populated only when the request had ``debug=True``.
    steps: Optional[list[dict[str, Any]]] = None
    traversal_path: Optional[list[str]] = None
    knowledge_concepts: Optional[list[dict[str, Any]]] = None
    tools_referenced: Optional[list[dict[str, Any]]] = None


class GraphStats(_Base):
    total_nodes: int = 0
    total_edges: int = 0
    nodes_by_type: dict[str, int] = Field(default_factory=dict)
    edges_by_type: dict[str, int] = Field(default_factory=dict)
    backends: dict[str, Any] = Field(default_factory=dict)


class SearchResult(_Base):
    node_id: str
    node_type: str
    heading: str
    kb_source: str
    score: float


class SearchResponse(_Base):
    query: str
    results: list[SearchResult] = Field(default_factory=list)


class NodeDetail(_Base):
    node: dict[str, Any]
    edges: list[dict[str, Any]] = Field(default_factory=list)
    children: list[dict[str, Any]] = Field(default_factory=list)
    parent: Optional[str] = None


class TraverseResponse(_Base):
    root_node_id: str
    traversal_path: list[str] = Field(default_factory=list)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edge_count: int = 0


class ChatSessionSummary(_Base):
    session_id: str
    title: Optional[str] = None
    message_count: int = 0
    created_at: datetime
    last_activity_at: datetime


class ChatMessage(_Base):
    id: int
    role: str
    query: Optional[str] = None
    response: Optional[dict[str, Any]] = None
    intent: Optional[str] = None
    kb_focus: Optional[str] = None
    created_at: datetime
    duration_ms: int = 0


class ChatSessionDetail(_Base):
    session_id: str
    workspace_id: str
    title: Optional[str] = None
    messages: list[ChatMessage] = Field(default_factory=list)


class BuildJobSummary(_Base):
    job_id: str
    workspace_id: str
    status: str
    stage: int
    stage_name: str
    percent: int
    created_at: datetime
    finished_at: Optional[datetime] = None
