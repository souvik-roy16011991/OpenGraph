"""
Shared query pipeline used by both the internal ``POST /query`` endpoint
(JWT auth, interactive UI) and the public ``POST /api/v1/ext/query``
endpoint (API-key auth, third-party callers).

Keeping the pipeline in one place guarantees both surfaces see the same
LangGraph agent behaviour, the same usage accounting, the same audit
shape, and the same billing rules. The callers only differ in how they
resolve the identity — JWT user vs. API-key owner — and which audit verb
they emit.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, AsyncGenerator, Optional

from fastapi import HTTPException

from src.api.routes import (
    ChatUsage,
    QueryRequest,
    QueryResponse,
    _get_kg,
    _persist_chat_turn,
    _preview_for,
)
from src.infra.audit import record_audit

logger = logging.getLogger(__name__)


async def run_query(
    *,
    workspace_id: str,
    req: QueryRequest,
    owner_user_id: uuid.UUID,
    actor_type: str = "system",
    audit_action: str = "chat.query",
    audit_metadata_extra: Optional[dict] = None,
    billing_source_type: str = "chat_session",
    billing_reason: str = "chat",
) -> QueryResponse:
    """Execute the agent pipeline and return a :class:`QueryResponse`.

    Parameters:
      workspace_id: target workspace UUID (caller has already verified
        ownership — this function does NOT re-check).
      req: the parsed :class:`QueryRequest`.
      owner_user_id: the principal whose credits/trial counter are debited.
        For JWT callers this is ``user.id``; for API-key callers it's the
        key's ``user_id`` (resolved in ext_routes).
      actor_type: audit field. ``"user"`` for JWT calls; ``"api_key"`` for
        the ext surface. Does not change agent behaviour.
      audit_action: the verb written to ``user_audit_log``. Use
        ``"chat.query"`` for the internal endpoint and ``"api.query"`` for
        the ext endpoint so operators can filter per-source in the audit.
      audit_metadata_extra: merged into the audit metadata dict. Ext
        callers put the API key id here.
      billing_source_type / billing_reason: ledger fields.

    Behaviour is byte-for-byte identical to the previous in-route
    implementation — this refactor is a pure extract, no semantic change.
    """
    from src.agent.graph import KBGraphAgent
    from src.billing import check_chat_allowed
    from src.config import LLM_MODEL, USE_NEON
    from src.infra.workspace_llm import get_workspace_llm_model
    from src.observability.usage import ChatMetrics, chat_metrics_var

    await check_chat_allowed(owner_user_id)

    kg = await _get_kg(workspace_id)
    agent = KBGraphAgent.from_graph(kg)

    session_id = req.session_id or str(uuid.uuid4())

    resolved_model = (req.llm_model or "").strip() or None
    if resolved_model is None:
        resolved_model = await get_workspace_llm_model(workspace_id)
    effective_model = resolved_model or LLM_MODEL

    chat_metrics = ChatMetrics(model=effective_model)
    chat_token = chat_metrics_var.set(chat_metrics)

    start = time.perf_counter()
    try:
        try:
            state = agent.query(req.query, llm_model=resolved_model)
        except Exception as exc:
            logger.error(f"Agent query failed: {exc}", exc_info=True)
            if USE_NEON:
                try:
                    await _persist_chat_turn(
                        workspace_id=workspace_id,
                        session_id=session_id,
                        query=req.query,
                        resp=None,
                        duration_ms=int((time.perf_counter() - start) * 1000),
                        error=str(exc),
                    )
                except Exception:
                    pass
            raise HTTPException(status_code=500, detail=str(exc))
    finally:
        try:
            chat_metrics_var.reset(chat_token)
        except Exception:
            pass
    duration_ms = int((time.perf_counter() - start) * 1000)

    usage_payload = ChatUsage(**chat_metrics.usage_dict()) if (
        chat_metrics.llm_calls
        or chat_metrics.llm_prompt_tokens
        or chat_metrics.llm_completion_tokens
    ) else None

    resp = QueryResponse(
        query=state["query"],
        intent=state.get("intent", ""),
        kb_focus=state.get("kb_focus", "both"),
        extracted_topics=state.get("extracted_topics", []),
        response=state.get("response", ""),
        steps=state.get("steps", []),
        tools_referenced=state.get("tools_referenced", []),
        knowledge_concepts=state.get("knowledge_concepts", []),
        follow_up_suggestions=state.get("follow_up_suggestions", []),
        traversal_path=state.get("traversal_path", []),
        session_id=session_id,
        duration_ms=duration_ms,
        error=state.get("error"),
        llm_model=effective_model,
        usage=usage_payload,
    )

    if USE_NEON:
        persisted = False
        last_err: Optional[Exception] = None
        for _attempt in range(2):
            try:
                await _persist_chat_turn(
                    workspace_id=workspace_id,
                    session_id=session_id,
                    query=req.query,
                    resp=resp,
                    duration_ms=duration_ms,
                )
                persisted = True
                break
            except Exception as exc:
                last_err = exc
        if not persisted:
            logger.warning(
                "Neon chat persistence failed after retry for session %s: %s",
                session_id, last_err,
            )
            resp.history_persisted = False

    audit_md = {
        "model": effective_model,
        "intent": resp.intent,
        "duration_ms": duration_ms,
        "query_preview": (req.query[:80] + "…") if len(req.query) > 80 else req.query,
    }
    if audit_metadata_extra:
        audit_md.update(audit_metadata_extra)
    record_audit(
        owner_user_id, audit_action,
        target_type="chat_session", target_id=session_id,
        workspace_id=workspace_id,
        metadata=audit_md,
    )

    # Billing — post-response so a debit failure never blocks the user's
    # answer. BYOK applies when the workspace has its own OpenRouter key on
    # file; the ext surface honours the same waiver.
    try:
        from sqlalchemy import select as _select
        from src.billing import cost_chat, debit
        from src.billing.ledger import bump_trial_counter
        from src.infra.db import get_session as _get_session
        from src.infra.db_models import WorkspaceApiKey as _WorkspaceApiKey
        ws_uuid = uuid.UUID(workspace_id)
        async with _get_session() as _s:
            has_byok = (await _s.execute(
                _select(_WorkspaceApiKey.workspace_id).where(
                    _WorkspaceApiKey.workspace_id == ws_uuid
                )
            )).scalar_one_or_none() is not None
        usage_dict = usage_payload.model_dump() if usage_payload else {}
        credits = cost_chat(usage=usage_dict, has_byok=has_byok)
        await debit(
            owner_user_id,
            credits,
            reason=billing_reason,
            source_type=billing_source_type,
            source_id=session_id,
            actor_type=actor_type,
            metadata={
                "workspace_id": workspace_id,
                "has_byok": has_byok,
                "model": effective_model,
            },
        )
        await bump_trial_counter(owner_user_id, kind="chat")
    except Exception as bill_exc:
        logger.warning(
            "chat billing debit failed for session %s (actor=%s): %s",
            session_id, actor_type, bill_exc,
        )

    return resp


async def run_query_streaming(
    *,
    workspace_id: str,
    req: QueryRequest,
    owner_user_id: uuid.UUID,
    actor_type: str = "user",
    audit_action: str = "chat.query",
    audit_metadata_extra: Optional[dict] = None,
    billing_source_type: str = "chat_session",
    billing_reason: str = "chat",
) -> AsyncGenerator[dict[str, Any], None]:
    """Streaming variant of :func:`run_query`.

    Yields one progress dict per LangGraph node completion, then a final
    terminal frame. Frame shapes:

      * Stage frame: ``{"stage": <node_name>, "label": str, "preview": str}``
      * Terminal success: ``{"stage": "done", "result": <QueryResponse JSON>}``
      * Terminal error:   ``{"stage": "error", "error": str, "status"?: int}``

    Persistence, audit and billing run after the final node completes
    and before the ``done`` frame is yielded — same ordering as
    :func:`run_query`. The ``result`` field of the ``done`` frame is the
    same :class:`QueryResponse` that the JSON endpoint would return for
    the same input.
    """
    from src.agent.graph import KBGraphAgent
    from src.billing import check_chat_allowed
    from src.config import LLM_MODEL, USE_NEON
    from src.infra.workspace_llm import get_workspace_llm_model
    from src.observability.usage import ChatMetrics, chat_metrics_var

    # Pre-checks (402 quota etc.) — surface as error frame so the SSE
    # response body contains a structured signal instead of bubbling an
    # HTTPException into the wrapping StreamingResponse.
    try:
        await check_chat_allowed(owner_user_id)
    except HTTPException as exc:
        yield {"stage": "error", "error": str(exc.detail), "status": exc.status_code}
        return

    kg = await _get_kg(workspace_id)
    agent = KBGraphAgent.from_graph(kg)

    session_id = req.session_id or str(uuid.uuid4())

    resolved_model = (req.llm_model or "").strip() or None
    if resolved_model is None:
        resolved_model = await get_workspace_llm_model(workspace_id)
    effective_model = resolved_model or LLM_MODEL

    chat_metrics = ChatMetrics(model=effective_model)
    chat_token = chat_metrics_var.set(chat_metrics)

    accumulated: dict[str, Any] = dict(agent.initial_state(req.query, resolved_model))
    start = time.perf_counter()
    stream_failed: Optional[Exception] = None

    try:
        try:
            async for chunk in agent.astream(req.query, llm_model=resolved_model):
                # LangGraph ``updates`` mode yields {node_name: delta_dict}
                # each tick. A node that returns nothing still appears as
                # an empty dict. Conditional edges don't emit chunks.
                for node_name, delta in chunk.items():
                    if isinstance(delta, dict):
                        accumulated.update(delta)
                    label, preview = _preview_for(node_name, accumulated)
                    yield {"stage": node_name, "label": label, "preview": preview}
        except Exception as exc:
            stream_failed = exc
            logger.error(f"Agent streaming query failed: {exc}", exc_info=True)
    finally:
        try:
            chat_metrics_var.reset(chat_token)
        except Exception:
            pass

    duration_ms = int((time.perf_counter() - start) * 1000)

    if stream_failed is not None:
        if USE_NEON:
            try:
                await _persist_chat_turn(
                    workspace_id=workspace_id,
                    session_id=session_id,
                    query=req.query,
                    resp=None,
                    duration_ms=duration_ms,
                    error=str(stream_failed),
                )
            except Exception:
                pass
        yield {"stage": "error", "error": str(stream_failed)}
        return

    state = accumulated

    usage_payload = ChatUsage(**chat_metrics.usage_dict()) if (
        chat_metrics.llm_calls
        or chat_metrics.llm_prompt_tokens
        or chat_metrics.llm_completion_tokens
    ) else None

    resp = QueryResponse(
        query=state["query"],
        intent=state.get("intent", ""),
        kb_focus=state.get("kb_focus", "both"),
        extracted_topics=state.get("extracted_topics", []),
        response=state.get("response", ""),
        steps=state.get("steps", []),
        tools_referenced=state.get("tools_referenced", []),
        knowledge_concepts=state.get("knowledge_concepts", []),
        follow_up_suggestions=state.get("follow_up_suggestions", []),
        traversal_path=state.get("traversal_path", []),
        session_id=session_id,
        duration_ms=duration_ms,
        error=state.get("error"),
        llm_model=effective_model,
        usage=usage_payload,
    )

    if USE_NEON:
        persisted = False
        last_err: Optional[Exception] = None
        for _attempt in range(2):
            try:
                await _persist_chat_turn(
                    workspace_id=workspace_id,
                    session_id=session_id,
                    query=req.query,
                    resp=resp,
                    duration_ms=duration_ms,
                )
                persisted = True
                break
            except Exception as exc:
                last_err = exc
        if not persisted:
            logger.warning(
                "Neon chat persistence failed after retry for session %s: %s",
                session_id, last_err,
            )
            resp.history_persisted = False

    audit_md = {
        "model": effective_model,
        "intent": resp.intent,
        "duration_ms": duration_ms,
        "query_preview": (req.query[:80] + "…") if len(req.query) > 80 else req.query,
        "stream": True,
    }
    if audit_metadata_extra:
        audit_md.update(audit_metadata_extra)
    record_audit(
        owner_user_id, audit_action,
        target_type="chat_session", target_id=session_id,
        workspace_id=workspace_id,
        metadata=audit_md,
    )

    try:
        from sqlalchemy import select as _select
        from src.billing import cost_chat, debit
        from src.billing.ledger import bump_trial_counter
        from src.infra.db import get_session as _get_session
        from src.infra.db_models import WorkspaceApiKey as _WorkspaceApiKey
        ws_uuid = uuid.UUID(workspace_id)
        async with _get_session() as _s:
            has_byok = (await _s.execute(
                _select(_WorkspaceApiKey.workspace_id).where(
                    _WorkspaceApiKey.workspace_id == ws_uuid
                )
            )).scalar_one_or_none() is not None
        usage_dict = usage_payload.model_dump() if usage_payload else {}
        credits = cost_chat(usage=usage_dict, has_byok=has_byok)
        await debit(
            owner_user_id,
            credits,
            reason=billing_reason,
            source_type=billing_source_type,
            source_id=session_id,
            actor_type=actor_type,
            metadata={
                "workspace_id": workspace_id,
                "has_byok": has_byok,
                "model": effective_model,
            },
        )
        await bump_trial_counter(owner_user_id, kind="chat")
    except Exception as bill_exc:
        logger.warning(
            "chat streaming billing debit failed for session %s (actor=%s): %s",
            session_id, actor_type, bill_exc,
        )

    yield {"stage": "done", "result": resp.model_dump()}
