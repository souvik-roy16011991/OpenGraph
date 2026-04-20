"""
LLM catalog + per-workspace model selection endpoints.

GET  /api/v1/llm/models                         — list models from OpenRouter
                                                  (cached 1h in Upstash; filtered
                                                  by OPENROUTER_ALLOWED_MODELS)
GET  /api/v1/workspaces/{id}/llm                — read workspace default
PUT  /api/v1/workspaces/{id}/llm  {model}       — set workspace default; pass
                                                  null/empty to clear
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.config import (
    EMBEDDING_MODEL,
    LLM_MODEL,
    OPENROUTER_ALLOWED_MODELS,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
)
from src.api.auth import require_user
from src.infra import upstash
from src.infra.audit import record_audit
from src.infra.db_models import User
from src.infra.workspace_llm import get_workspace_llm_model, set_workspace_llm_model

logger = logging.getLogger(__name__)

router = APIRouter()

_MODELS_CACHE_KEY = "llm:openrouter:models:v1"
_EMBED_MODELS_CACHE_KEY = "llm:openrouter:embedding-models:v2"
_MODELS_CACHE_TTL = 3600  # 1 hour


# OpenRouter's public /models endpoint is chat-only — their embedding models
# (reachable via the same `<provider>/<model>` id on the embeddings API) are
# not enumerated there. Keep a curated baseline so the picker has something to
# show; any catalog hits (if OR ever adds them) are unioned in.
_CURATED_EMBEDDING_MODELS: list[dict[str, Any]] = [
    {"id": "qwen/qwen3-embedding-8b",         "name": "Qwen3 Embedding 8B",         "description": "Qwen 4096-d multilingual embedding (default)."},
    {"id": "qwen/qwen3-embedding-4b",         "name": "Qwen3 Embedding 4B",         "description": "Qwen 2560-d embedding (smaller/cheaper)."},
    {"id": "qwen/qwen3-embedding-0.6b",       "name": "Qwen3 Embedding 0.6B",       "description": "Qwen 1024-d embedding (cheapest)."},
    {"id": "openai/text-embedding-3-small",   "name": "OpenAI text-embedding-3-small", "description": "1536-d (Matryoshka-resizable). Fast + cheap."},
    {"id": "openai/text-embedding-3-large",   "name": "OpenAI text-embedding-3-large", "description": "3072-d (Matryoshka-resizable). Highest quality."},
    {"id": "openai/text-embedding-ada-002",   "name": "OpenAI text-embedding-ada-002", "description": "Legacy 1536-d embedding."},
    {"id": "voyage/voyage-3",                 "name": "Voyage 3",                   "description": "1024-d general-purpose."},
    {"id": "voyage/voyage-3-large",           "name": "Voyage 3 Large",             "description": "2048-d, top-tier retrieval quality."},
    {"id": "voyage/voyage-code-3",            "name": "Voyage Code 3",              "description": "Code-specialised 1024-d."},
    {"id": "cohere/embed-english-v3.0",       "name": "Cohere Embed English v3",    "description": "1024-d English-tuned."},
    {"id": "cohere/embed-multilingual-v3.0",  "name": "Cohere Embed Multilingual v3","description": "1024-d multilingual."},
    {"id": "google/gemini-embedding-001",     "name": "Gemini Embedding 001",       "description": "Google 3072-d."},
]


def _is_embedding_model(model: dict) -> bool:
    """OpenRouter's catalog is chat-only today, but stay forward-compatible:
    match on id substring or architecture.output_modalities."""
    mid = (model.get("id") or "").lower()
    if "embed" in mid:
        return True
    arch = model.get("architecture") or {}
    out_mods = arch.get("output_modalities") or []
    if isinstance(out_mods, list) and any("embed" in str(m).lower() for m in out_mods):
        return True
    return False


def _allowed_set() -> set[str] | None:
    """Parse OPENROUTER_ALLOWED_MODELS into a set, or return None for 'allow all'."""
    raw = (OPENROUTER_ALLOWED_MODELS or "").strip()
    if not raw:
        return None
    return {m.strip() for m in raw.split(",") if m.strip()}


def _fetch_openrouter_models() -> list[dict[str, Any]]:
    """Call OpenRouter's /models endpoint; returns the raw ``data`` array."""
    url = f"{OPENROUTER_BASE_URL.rstrip('/')}/models"
    headers = {}
    if OPENROUTER_API_KEY:
        headers["Authorization"] = f"Bearer {OPENROUTER_API_KEY}"
    resp = httpx.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") or []
    if not isinstance(data, list):
        return []
    return data


def _summarize(model: dict[str, Any]) -> dict[str, Any]:
    """Keep only the fields the frontend needs — OpenRouter's raw payload is huge."""
    pricing = model.get("pricing") or {}
    ctx = model.get("context_length") or model.get("context")
    return {
        "id": model.get("id"),
        "name": model.get("name") or model.get("id"),
        "description": model.get("description"),
        "context_length": ctx,
        "pricing": {
            "prompt": pricing.get("prompt"),
            "completion": pricing.get("completion"),
        },
    }


class LLMModelsResponse(BaseModel):
    default: str
    models: list[dict[str, Any]]
    allowlist_active: bool


@router.get("/llm/models", response_model=LLMModelsResponse, summary="List available OpenRouter models")
def list_llm_models(refresh: bool = False):
    """Return OpenRouter's model catalog.

    - Cached in Upstash for 1h (``refresh=true`` busts the cache).
    - Filtered by ``OPENROUTER_ALLOWED_MODELS`` if that env var is set.
    - Falls through to a live OpenRouter call on any cache miss / error.
    """
    allowed = _allowed_set()
    data: Optional[list[dict[str, Any]]] = None

    if not refresh:
        cached = upstash.get_json(_MODELS_CACHE_KEY)
        if isinstance(cached, list):
            data = cached

    if data is None:
        try:
            raw = _fetch_openrouter_models()
            data = [_summarize(m) for m in raw if isinstance(m, dict)]
        except Exception as exc:
            logger.error("OpenRouter /models fetch failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"OpenRouter /models failed: {exc}")
        upstash.set_json(_MODELS_CACHE_KEY, data, ttl_seconds=_MODELS_CACHE_TTL)

    if allowed:
        data = [m for m in data if m.get("id") in allowed]

    return LLMModelsResponse(
        default=LLM_MODEL,
        models=data,
        allowlist_active=allowed is not None,
    )


class EmbeddingModelsResponse(BaseModel):
    default: str
    models: list[dict[str, Any]]
    allowlist_active: bool


@router.get(
    "/llm/embedding-models",
    response_model=EmbeddingModelsResponse,
    summary="List OpenRouter models that produce embeddings",
)
def list_embedding_models(refresh: bool = False):
    """Filter OpenRouter's catalog to embedding-capable models only.

    Separate cache key from `/llm/models` because the filter runs on the raw
    payload (architecture + id) before the summary strips it.
    """
    allowed = _allowed_set()
    data: Optional[list[dict[str, Any]]] = None

    if not refresh:
        cached = upstash.get_json(_EMBED_MODELS_CACHE_KEY)
        if isinstance(cached, list):
            data = cached

    if data is None:
        catalog_hits: list[dict[str, Any]] = []
        try:
            raw = _fetch_openrouter_models()
            catalog_hits = [_summarize(m) for m in raw if isinstance(m, dict) and _is_embedding_model(m)]
        except Exception as exc:
            # A 4xx/5xx from OR is not fatal — the curated list alone is useful.
            logger.warning("OpenRouter /models fetch failed for embedding filter: %s", exc)

        # Union curated + catalog (catalog wins on id collision so we pick up
        # live pricing / context_length updates).
        merged: dict[str, dict[str, Any]] = {m["id"]: dict(m) for m in _CURATED_EMBEDDING_MODELS}
        for m in catalog_hits:
            if m.get("id"):
                merged[m["id"]] = m
        data = sorted(merged.values(), key=lambda m: m["id"])
        upstash.set_json(_EMBED_MODELS_CACHE_KEY, data, ttl_seconds=_MODELS_CACHE_TTL)

    if allowed:
        data = [m for m in data if m.get("id") in allowed]

    return EmbeddingModelsResponse(
        default=EMBEDDING_MODEL,
        models=data,
        allowlist_active=allowed is not None,
    )


class WorkspaceLLMResponse(BaseModel):
    workspace_id: str
    llm_model: Optional[str]
    effective_model: str


class WorkspaceLLMRequest(BaseModel):
    model: Optional[str] = None  # null / "" clears the preference


def _validate_workspace_uuid(workspace_id: uuid.UUID) -> str:
    return str(workspace_id)


@router.get("/workspaces/{workspace_id}/llm", response_model=WorkspaceLLMResponse,
            summary="Read a workspace's LLM preference")
async def get_workspace_llm(workspace_id: uuid.UUID):
    wid = _validate_workspace_uuid(workspace_id)
    chosen = await get_workspace_llm_model(wid)
    return WorkspaceLLMResponse(
        workspace_id=wid,
        llm_model=chosen,
        effective_model=chosen or LLM_MODEL,
    )


@router.put("/workspaces/{workspace_id}/llm", response_model=WorkspaceLLMResponse,
            summary="Set or clear a workspace's LLM preference")
async def put_workspace_llm(
    workspace_id: uuid.UUID,
    body: WorkspaceLLMRequest,
    user: User = Depends(require_user),
):
    wid = _validate_workspace_uuid(workspace_id)

    # When a model id is provided, validate it against the (possibly filtered)
    # OpenRouter catalog. This catches typos and keeps the allowlist enforced.
    model = (body.model or "").strip() or None
    if model:
        try:
            catalog = list_llm_models()  # uses Upstash cache; runs in sync context
            valid_ids = {m["id"] for m in catalog.models if m.get("id")}
            if valid_ids and model not in valid_ids:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Model {model!r} is not available"
                        + (" in the allowlist." if catalog.allowlist_active else " on OpenRouter.")
                    ),
                )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Model validation skipped (catalog unreachable): %s", exc)

    try:
        await set_workspace_llm_model(wid, model)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    record_audit(
        user.id, "llm.preference.change",
        target_type="workspace", target_id=wid,
        workspace_id=wid,
        metadata={"model": model},
    )
    return WorkspaceLLMResponse(
        workspace_id=wid,
        llm_model=model,
        effective_model=model or LLM_MODEL,
    )
