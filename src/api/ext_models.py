"""
Public (third-party-safe) request + response models for /api/v1/ext/*.

Why a parallel set of models instead of reusing ``QueryResponse``:

  - The internal ``QueryResponse`` carries three ``list[dict[str, Any]]``
    escape fields — ``steps``, ``tools_referenced``, ``knowledge_concepts``
    — plus a ``traversal_path`` of raw internal node IDs. These shapes
    change as the LangGraph agent evolves; locking them into an SDK
    contract would force breaking changes every time the agent does.
  - Third parties shouldn't see internal reasoning steps by default; the
    SDK surface is "question in, answer out, plus optional structured
    metadata." Debug-mode is opt-in via ``debug=true``.
  - We want RFC 7807 problem-details for 4xx/5xx errors so SDK error
    handling is predictable across languages.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

class ExtQueryRequest(BaseModel):
    """Public query request. A strict subset of :class:`QueryRequest`."""

    workspace_id: str = Field(description="Target workspace UUID. Must be owned by the API key's owner.")
    query: str = Field(description="Natural-language question for the graph.")
    session_id: Optional[str] = Field(
        default=None,
        description=(
            "Continue an existing chat session. Omit to start a new one — the "
            "response's ``session_id`` is the handle to reuse on follow-up calls."
        ),
    )
    llm_model: Optional[str] = Field(
        default=None,
        description=(
            "Override the LLM model for this single call. Must be a valid "
            "OpenRouter model id. Falls back to the workspace default when omitted."
        ),
    )
    debug: bool = Field(
        default=False,
        description=(
            "When true, the response includes the internal agent reasoning "
            "steps and traversal path. Shape is not guaranteed stable across "
            "releases — intended for debugging integrations."
        ),
    )


class ExtChatUsage(BaseModel):
    """Per-turn LLM token usage. Matches the internal shape one-for-one."""
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_total_tokens: int = 0
    llm_calls: int = 0
    model: Optional[str] = None


class ExtQueryResponse(BaseModel):
    """Public query response. Stable across v1."""

    session_id: str = Field(description="Handle to continue this conversation; pass on follow-up calls.")
    response: str = Field(description="The assistant's answer, as markdown.")
    intent: str = Field(description="Classified user intent (e.g. 'how_to', 'lookup').")
    kb_focus: str = Field(
        description="Which KB the agent focused on: 'knowledge' | 'tool' | 'both'."
    )
    extracted_topics: list[str] = Field(default_factory=list)
    follow_up_suggestions: list[str] = Field(default_factory=list)
    llm_model: Optional[str] = Field(
        default=None,
        description="The resolved OpenRouter model id that actually answered.",
    )
    usage: Optional[ExtChatUsage] = Field(
        default=None,
        description="Token usage for this turn. Absent when no billable LLM call happened.",
    )
    duration_ms: int = Field(description="Server-measured wall time for the turn.")
    history_persisted: bool = Field(
        default=True,
        description=(
            "True if the turn was saved to chat history. When false the answer "
            "was produced but the DB write failed — retry the request with the "
            "same session_id to reconcile."
        ),
    )

    # Debug-only fields. Populated when the request had ``debug=true``.
    # Shape is ``list[dict[str, Any]]`` deliberately — these fields exist
    # for troubleshooting, not for stable downstream consumption.
    steps: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Internal agent reasoning steps. Only present when ``debug=true`` was set.",
    )
    traversal_path: Optional[list[str]] = Field(
        default=None,
        description="Node ids the agent visited. Only present when ``debug=true`` was set.",
    )
    knowledge_concepts: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Knowledge concepts surfaced mid-reasoning. Only present when ``debug=true`` was set.",
    )
    tools_referenced: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Tool nodes referenced mid-reasoning. Only present when ``debug=true`` was set.",
    )


# ---------------------------------------------------------------------------
# Graph reads
# ---------------------------------------------------------------------------

class ExtGraphStats(BaseModel):
    total_nodes: int = 0
    total_edges: int = 0
    nodes_by_type: dict[str, int] = Field(default_factory=dict)
    edges_by_type: dict[str, int] = Field(default_factory=dict)
    backends: dict[str, Any] = Field(default_factory=dict)


class ExtSearchResult(BaseModel):
    node_id: str
    node_type: str
    heading: str
    kb_source: str
    score: float


class ExtSearchResponse(BaseModel):
    query: str
    results: list[ExtSearchResult]


class ExtNodeDetail(BaseModel):
    node: dict[str, Any]
    edges: list[dict[str, Any]]
    children: list[dict[str, Any]]
    parent: Optional[str] = None


class ExtTraverseRequest(BaseModel):
    workspace_id: str
    node_id: str
    max_depth: int = Field(default=3, ge=1, le=10)
    edge_types: Optional[list[str]] = None


class ExtTraverseResponse(BaseModel):
    root_node_id: str
    traversal_path: list[str]
    nodes: list[dict[str, Any]]
    edge_count: int


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

class ExtChatSessionSummary(BaseModel):
    session_id: str
    title: Optional[str] = None
    message_count: int = 0
    created_at: datetime
    last_activity_at: datetime


class ExtChatSessionsResponse(BaseModel):
    sessions: list[ExtChatSessionSummary]


class ExtChatMessage(BaseModel):
    id: int
    role: str
    query: Optional[str] = None
    response: Optional[dict[str, Any]] = None
    intent: Optional[str] = None
    kb_focus: Optional[str] = None
    created_at: datetime
    duration_ms: int = 0


class ExtChatSessionDetail(BaseModel):
    session_id: str
    workspace_id: str
    title: Optional[str] = None
    messages: list[ExtChatMessage]


class ExtBuildJobSummary(BaseModel):
    job_id: str
    workspace_id: str
    status: str
    stage: int
    stage_name: str
    percent: int
    created_at: datetime
    finished_at: Optional[datetime] = None


class ExtBuildJobsResponse(BaseModel):
    builds: list[ExtBuildJobSummary]


# ---------------------------------------------------------------------------
# RFC 7807 problem-details error envelope
# ---------------------------------------------------------------------------

class ProblemDetails(BaseModel):
    """RFC 7807 error body used for every 4xx/5xx response on /ext/*.

    Example::

        {
          "type": "https://opengraph.example/errors/rate-limit",
          "title": "Too Many Requests",
          "status": 429,
          "detail": "Rate limit exceeded — 60 requests per minute.",
          "request_id": "01HX…",
          "retry_after_seconds": 42
        }
    """
    type: str = "about:blank"
    title: str
    status: int
    detail: Optional[str] = None
    request_id: Optional[str] = None
    retry_after_seconds: Optional[int] = None
