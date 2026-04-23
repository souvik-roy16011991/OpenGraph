"""
Official Python SDK for the OpenGraph developer API.

Quick start::

    from opengraph_sdk import Client

    client = Client(api_key="og_live_...")  # or env OPENGRAPH_API_KEY
    resp = client.query(workspace_id="...", query="What's my churn rate?")
    print(resp.response)

Async usage mirrors the sync client::

    from opengraph_sdk import AsyncClient

    async with AsyncClient() as client:
        resp = await client.query(workspace_id="...", query="…")

All responses are strongly typed via :mod:`pydantic`. Errors raise
subclasses of :class:`OpenGraphError` — see :mod:`opengraph_sdk.errors`.
"""

from opengraph_sdk.client import AsyncClient, Client
from opengraph_sdk.errors import (
    AuthError,
    NotFoundError,
    OpenGraphError,
    RateLimitError,
    ServerError,
    ValidationError,
)
from opengraph_sdk.models import (
    BuildJobSummary,
    ChatMessage,
    ChatSessionDetail,
    ChatSessionSummary,
    ChatUsage,
    GraphStats,
    NodeDetail,
    QueryResponse,
    SearchResponse,
    SearchResult,
    TraverseResponse,
)

__version__ = "0.1.0"

__all__ = [
    "Client",
    "AsyncClient",
    "OpenGraphError",
    "AuthError",
    "RateLimitError",
    "NotFoundError",
    "ServerError",
    "ValidationError",
    "QueryResponse",
    "ChatUsage",
    "GraphStats",
    "SearchResponse",
    "SearchResult",
    "NodeDetail",
    "TraverseResponse",
    "ChatSessionSummary",
    "ChatSessionDetail",
    "ChatMessage",
    "BuildJobSummary",
    "__version__",
]
