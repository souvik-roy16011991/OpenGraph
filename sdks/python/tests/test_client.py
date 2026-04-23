"""Unit tests for the Python SDK using `respx` mocks.

Run with:  pip install -e ".[dev]" && pytest

These tests verify client-side contract only (payload shapes + error
mapping). A contract test against a real backend lives in
``examples/query.py``.
"""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from opengraph_sdk import AuthError, Client, NotFoundError, RateLimitError


BASE = "http://test.example"
API_KEY = "og_live_test"


def _client() -> Client:
    return Client(api_key=API_KEY, base_url=BASE, max_retries=0)


def test_query_happy_path() -> None:
    with respx.mock:
        respx.post(f"{BASE}/api/v1/ext/query").mock(
            return_value=Response(
                200,
                json={
                    "session_id": "sess-1",
                    "response": "hello world",
                    "intent": "greet",
                    "kb_focus": "both",
                    "extracted_topics": [],
                    "follow_up_suggestions": ["what next?"],
                    "llm_model": "test-model",
                    "usage": {
                        "llm_prompt_tokens": 10,
                        "llm_completion_tokens": 5,
                        "llm_total_tokens": 15,
                        "llm_calls": 1,
                    },
                    "duration_ms": 42,
                    "history_persisted": True,
                },
            )
        )
        with _client() as c:
            resp = c.query("hi", workspace_id="ws-1")
    assert resp.session_id == "sess-1"
    assert resp.response == "hello world"
    assert resp.usage is not None
    assert resp.usage.llm_total_tokens == 15
    assert resp.follow_up_suggestions == ["what next?"]


def test_auth_error_maps_to_auth_error() -> None:
    with respx.mock:
        respx.get(f"{BASE}/api/v1/ext/graph/stats").mock(
            return_value=Response(401, json={"detail": "Invalid or missing API key."})
        )
        with _client() as c:
            with pytest.raises(AuthError) as info:
                c.graph.stats(workspace_id="ws-1")
    assert info.value.status == 401
    assert "Invalid" in (info.value.detail or "")


def test_not_found_error() -> None:
    with respx.mock:
        respx.get(f"{BASE}/api/v1/ext/graph/node/missing").mock(
            return_value=Response(404, json={"detail": "Node 'missing' not found."})
        )
        with _client() as c:
            with pytest.raises(NotFoundError):
                c.graph.node("missing", workspace_id="ws-1")


def test_rate_limit_includes_retry_after() -> None:
    with respx.mock:
        respx.get(f"{BASE}/api/v1/ext/graph/stats").mock(
            return_value=Response(
                429,
                json={"detail": "Rate limit exceeded — 60 per minute."},
                headers={"Retry-After": "42"},
            )
        )
        with _client() as c:
            with pytest.raises(RateLimitError) as info:
                c.graph.stats(workspace_id="ws-1")
    assert info.value.retry_after_seconds == 42


def test_workspace_id_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENGRAPH_WORKSPACE_ID", "ws-from-env")
    captured: dict[str, object] = {}

    with respx.mock:
        def _capture(request):
            captured["body"] = request.read()
            return Response(
                200,
                json={
                    "session_id": "sess-1", "response": "ok", "intent": "",
                    "kb_focus": "both", "extracted_topics": [],
                    "follow_up_suggestions": [], "duration_ms": 0,
                    "history_persisted": True,
                },
            )
        respx.post(f"{BASE}/api/v1/ext/query").mock(side_effect=_capture)
        with Client(api_key=API_KEY, base_url=BASE, max_retries=0) as c:
            c.query("hi")
    import json
    payload = json.loads(captured["body"])  # type: ignore[arg-type]
    assert payload["workspace_id"] == "ws-from-env"
