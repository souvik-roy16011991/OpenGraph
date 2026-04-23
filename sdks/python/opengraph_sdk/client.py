"""
Sync + async clients for the OpenGraph developer API.

Both clients share their entire request/response code path via the
``_request`` helper; the sync variant wraps ``httpx.Client`` and the async
variant wraps ``httpx.AsyncClient``. Keeping them twins avoids the common
bug where the sync and async surfaces drift in retry / error semantics.

Every call routes through ``/api/v1/ext/*`` and carries the API key as a
bearer header. Responses are parsed into :mod:`opengraph_sdk.models`;
non-2xx responses are raised as subclasses of
:class:`opengraph_sdk.errors.OpenGraphError` with the parsed JSON body
attached for inspection.

Retry policy: a bounded backoff retries transient failures (429 /
502 / 503 / 504 / network errors). 4xx responses that aren't 429 raise
immediately — they signal caller-side bugs that retrying won't fix.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any, Optional

import httpx

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
    ChatSessionDetail,
    ChatSessionSummary,
    GraphStats,
    NodeDetail,
    QueryResponse,
    SearchResponse,
    TraverseResponse,
)

_DEFAULT_BASE_URL = "https://api.opengraph.example"
_DEFAULT_TIMEOUT_SECONDS = 60.0
_USER_AGENT = "opengraph-sdk-python/0.1.0 (+https://opengraph.example)"

# Retries cover the kinds of failure the client can safely replay.
# 401/404/400 aren't retried — the caller can't fix them by trying again.
_RETRY_STATUSES = {429, 502, 503, 504}
_DEFAULT_RETRIES = 2


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

def _raise_for_status(resp: httpx.Response) -> None:
    """Translate an error response into the right typed exception."""
    if resp.status_code < 400:
        return
    # Try to parse a JSON body; fall back to empty payload.
    payload: Any = None
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001 — body may be HTML / truncated / missing
        payload = None

    detail = None
    if isinstance(payload, dict):
        # FastAPI uses {"detail": "..."}; RFC 7807 payloads use "detail" too.
        detail = payload.get("detail") if isinstance(payload.get("detail"), str) else None
        if detail is None and "title" in payload:
            detail = payload.get("title")

    message = detail or f"HTTP {resp.status_code}"
    status = resp.status_code

    if status == 401:
        raise AuthError(message, status=status, detail=detail, payload=payload)
    if status == 404:
        raise NotFoundError(message, status=status, detail=detail, payload=payload)
    if status in (400, 422):
        raise ValidationError(message, status=status, detail=detail, payload=payload)
    if status == 429:
        retry_after = None
        ra = resp.headers.get("Retry-After")
        if ra and ra.isdigit():
            retry_after = int(ra)
        raise RateLimitError(
            message,
            retry_after_seconds=retry_after,
            status=status, detail=detail, payload=payload,
        )
    if status >= 500:
        raise ServerError(message, status=status, detail=detail, payload=payload)
    raise OpenGraphError(message, status=status, detail=detail, payload=payload)


def _sleep_for_attempt(attempt: int, resp: Optional[httpx.Response]) -> float:
    """Exponential backoff with a Retry-After override, jittered."""
    if resp is not None:
        ra = resp.headers.get("Retry-After")
        if ra and ra.isdigit():
            return max(1.0, float(int(ra)))
    base = min(30.0, 0.5 * (2 ** attempt))
    return base + random.uniform(0, 0.25 * base)


# ---------------------------------------------------------------------------
# Sync client
# ---------------------------------------------------------------------------

class _Namespace:
    """Tiny helper so ``client.graph.stats(...)`` reads naturally.

    Each resource namespace below is an instance of this bound to the
    parent client — keeps dotted access without duplicating transport
    logic.
    """


class Client:
    """Synchronous client for the OpenGraph developer API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        workspace_id: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_RETRIES,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENGRAPH_API_KEY", "")
        if not self._api_key:
            raise OpenGraphError(
                "Missing API key. Pass api_key=... or set OPENGRAPH_API_KEY.",
            )
        self._base_url = (
            base_url
            or os.environ.get("OPENGRAPH_BASE_URL")
            or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._default_workspace_id = (
            workspace_id or os.environ.get("OPENGRAPH_WORKSPACE_ID")
        )
        self._max_retries = max_retries
        self._http = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "User-Agent": _USER_AGENT,
            },
        )
        self.graph = _GraphNamespace(self)
        self.history = _HistoryNamespace(self)

    # Context-manager support — close the underlying HTTP client cleanly.
    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- Core request helper -------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        last_exc: Optional[Exception] = None
        last_resp: Optional[httpx.Response] = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._http.request(method, url, params=params, json=json)
            except (httpx.TransportError, httpx.ReadTimeout) as exc:
                last_exc = exc
                last_resp = None
                if attempt >= self._max_retries:
                    raise ServerError(f"Network error: {exc}", status=0) from exc
                time.sleep(_sleep_for_attempt(attempt, None))
                continue
            if resp.status_code in _RETRY_STATUSES and attempt < self._max_retries:
                last_resp = resp
                time.sleep(_sleep_for_attempt(attempt, resp))
                continue
            _raise_for_status(resp)
            return resp.json()
        # Should be unreachable — either we returned or we raised above.
        if last_exc:
            raise ServerError(f"Retry budget exhausted: {last_exc}", status=0) from last_exc
        if last_resp is not None:
            _raise_for_status(last_resp)
        raise ServerError("Retry budget exhausted.")

    # -- Workspace helper ----------------------------------------------------

    def _ws(self, explicit: Optional[str]) -> Optional[str]:
        return explicit or self._default_workspace_id

    # -- Endpoints -----------------------------------------------------------

    def query(
        self,
        query: str,
        *,
        workspace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        llm_model: Optional[str] = None,
        debug: bool = False,
    ) -> QueryResponse:
        """Ask the graph a natural-language question."""
        body = {
            "workspace_id": self._ws(workspace_id),
            "query": query,
            "session_id": session_id,
            "llm_model": llm_model,
            "debug": debug,
        }
        data = self._request("POST", "/api/v1/ext/query", json={k: v for k, v in body.items() if v is not None or k in ("debug",)})
        return QueryResponse.model_validate(data)


class _GraphNamespace:
    def __init__(self, client: Client) -> None:
        self._c = client

    def stats(self, *, workspace_id: Optional[str] = None) -> GraphStats:
        params = {}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        data = self._c._request("GET", "/api/v1/ext/graph/stats", params=params)
        return GraphStats.model_validate(data)

    def search(
        self,
        q: str,
        *,
        workspace_id: Optional[str] = None,
        top_k: int = 10,
        node_type: Optional[str] = None,
    ) -> SearchResponse:
        params: dict[str, Any] = {"q": q, "top_k": top_k}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        if node_type:
            params["node_type"] = node_type
        data = self._c._request("GET", "/api/v1/ext/graph/search", params=params)
        return SearchResponse.model_validate(data)

    def node(self, node_id: str, *, workspace_id: Optional[str] = None) -> NodeDetail:
        params = {}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        data = self._c._request(
            "GET", f"/api/v1/ext/graph/node/{node_id}", params=params,
        )
        return NodeDetail.model_validate(data)

    def traverse(
        self,
        node_id: str,
        *,
        workspace_id: Optional[str] = None,
        max_depth: int = 3,
        edge_types: Optional[list[str]] = None,
    ) -> TraverseResponse:
        body: dict[str, Any] = {
            "workspace_id": self._c._ws(workspace_id),
            "node_id": node_id,
            "max_depth": max_depth,
        }
        if edge_types:
            body["edge_types"] = edge_types
        data = self._c._request("POST", "/api/v1/ext/graph/traverse", json=body)
        return TraverseResponse.model_validate(data)

    def tree(self, *, workspace_id: Optional[str] = None) -> dict[str, Any]:
        params = {}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        return self._c._request("GET", "/api/v1/ext/graph/tree", params=params)

    def tools(
        self,
        *,
        workspace_id: Optional[str] = None,
        category: Optional[str] = None,
        provider: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        if category:
            params["category"] = category
        if provider:
            params["provider"] = provider
        if search:
            params["search"] = search
        return self._c._request("GET", "/api/v1/ext/graph/tools", params=params)

    def chapters(self, *, workspace_id: Optional[str] = None) -> dict[str, Any]:
        params = {}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        return self._c._request("GET", "/api/v1/ext/graph/chapters", params=params)


class _HistoryNamespace:
    def __init__(self, client: Client) -> None:
        self._c = client

    def chats(
        self,
        *,
        workspace_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[ChatSessionSummary]:
        params: dict[str, Any] = {"limit": limit}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        data = self._c._request("GET", "/api/v1/ext/history/chats", params=params)
        return [ChatSessionSummary.model_validate(s) for s in data.get("sessions", [])]

    def chat(
        self,
        session_id: str,
        *,
        workspace_id: Optional[str] = None,
    ) -> ChatSessionDetail:
        params = {}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        data = self._c._request(
            "GET", f"/api/v1/ext/history/chats/{session_id}", params=params,
        )
        return ChatSessionDetail.model_validate(data)

    def builds(
        self,
        *,
        workspace_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[BuildJobSummary]:
        params: dict[str, Any] = {"limit": limit}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        data = self._c._request("GET", "/api/v1/ext/history/builds", params=params)
        return [BuildJobSummary.model_validate(b) for b in data.get("builds", [])]

    def build(self, job_id: str) -> BuildJobSummary:
        data = self._c._request("GET", f"/api/v1/ext/history/builds/{job_id}")
        return BuildJobSummary.model_validate(data)


# ---------------------------------------------------------------------------
# Async client — mirror of Client via httpx.AsyncClient
# ---------------------------------------------------------------------------

class AsyncClient:
    """Asynchronous client for the OpenGraph developer API.

    Shares its code path with :class:`Client` via a parallel implementation.
    Prefer this when calling from inside an asyncio application.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        base_url: Optional[str] = None,
        workspace_id: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = _DEFAULT_RETRIES,
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENGRAPH_API_KEY", "")
        if not self._api_key:
            raise OpenGraphError(
                "Missing API key. Pass api_key=... or set OPENGRAPH_API_KEY.",
            )
        self._base_url = (
            base_url
            or os.environ.get("OPENGRAPH_BASE_URL")
            or _DEFAULT_BASE_URL
        ).rstrip("/")
        self._default_workspace_id = (
            workspace_id or os.environ.get("OPENGRAPH_WORKSPACE_ID")
        )
        self._max_retries = max_retries
        self._http = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "User-Agent": _USER_AGENT,
            },
        )
        self.graph = _AsyncGraphNamespace(self)
        self.history = _AsyncHistoryNamespace(self)

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    def _ws(self, explicit: Optional[str]) -> Optional[str]:
        return explicit or self._default_workspace_id

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> Any:
        import asyncio
        url = f"{self._base_url}{path}"
        last_exc: Optional[Exception] = None
        last_resp: Optional[httpx.Response] = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._http.request(method, url, params=params, json=json)
            except (httpx.TransportError, httpx.ReadTimeout) as exc:
                last_exc = exc
                last_resp = None
                if attempt >= self._max_retries:
                    raise ServerError(f"Network error: {exc}", status=0) from exc
                await asyncio.sleep(_sleep_for_attempt(attempt, None))
                continue
            if resp.status_code in _RETRY_STATUSES and attempt < self._max_retries:
                last_resp = resp
                await asyncio.sleep(_sleep_for_attempt(attempt, resp))
                continue
            _raise_for_status(resp)
            return resp.json()
        if last_exc:
            raise ServerError(f"Retry budget exhausted: {last_exc}", status=0) from last_exc
        if last_resp is not None:
            _raise_for_status(last_resp)
        raise ServerError("Retry budget exhausted.")

    async def query(
        self,
        query: str,
        *,
        workspace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        llm_model: Optional[str] = None,
        debug: bool = False,
    ) -> QueryResponse:
        body = {
            "workspace_id": self._ws(workspace_id),
            "query": query,
            "session_id": session_id,
            "llm_model": llm_model,
            "debug": debug,
        }
        data = await self._request(
            "POST", "/api/v1/ext/query",
            json={k: v for k, v in body.items() if v is not None or k in ("debug",)},
        )
        return QueryResponse.model_validate(data)


class _AsyncGraphNamespace:
    def __init__(self, client: AsyncClient) -> None:
        self._c = client

    async def stats(self, *, workspace_id: Optional[str] = None) -> GraphStats:
        params = {}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        data = await self._c._request("GET", "/api/v1/ext/graph/stats", params=params)
        return GraphStats.model_validate(data)

    async def search(
        self,
        q: str,
        *,
        workspace_id: Optional[str] = None,
        top_k: int = 10,
        node_type: Optional[str] = None,
    ) -> SearchResponse:
        params: dict[str, Any] = {"q": q, "top_k": top_k}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        if node_type:
            params["node_type"] = node_type
        data = await self._c._request("GET", "/api/v1/ext/graph/search", params=params)
        return SearchResponse.model_validate(data)

    async def node(self, node_id: str, *, workspace_id: Optional[str] = None) -> NodeDetail:
        params = {}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        data = await self._c._request(
            "GET", f"/api/v1/ext/graph/node/{node_id}", params=params,
        )
        return NodeDetail.model_validate(data)

    async def traverse(
        self,
        node_id: str,
        *,
        workspace_id: Optional[str] = None,
        max_depth: int = 3,
        edge_types: Optional[list[str]] = None,
    ) -> TraverseResponse:
        body: dict[str, Any] = {
            "workspace_id": self._c._ws(workspace_id),
            "node_id": node_id,
            "max_depth": max_depth,
        }
        if edge_types:
            body["edge_types"] = edge_types
        data = await self._c._request("POST", "/api/v1/ext/graph/traverse", json=body)
        return TraverseResponse.model_validate(data)


class _AsyncHistoryNamespace:
    def __init__(self, client: AsyncClient) -> None:
        self._c = client

    async def chats(
        self,
        *,
        workspace_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[ChatSessionSummary]:
        params: dict[str, Any] = {"limit": limit}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        data = await self._c._request("GET", "/api/v1/ext/history/chats", params=params)
        return [ChatSessionSummary.model_validate(s) for s in data.get("sessions", [])]

    async def builds(
        self,
        *,
        workspace_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[BuildJobSummary]:
        params: dict[str, Any] = {"limit": limit}
        wid = self._c._ws(workspace_id)
        if wid:
            params["workspace_id"] = wid
        data = await self._c._request("GET", "/api/v1/ext/history/builds", params=params)
        return [BuildJobSummary.model_validate(b) for b in data.get("builds", [])]
