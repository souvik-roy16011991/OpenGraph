"""
Per-run usage accounting for graph builds and chat sessions.

Two ContextVar-backed accumulators — ``build_metrics_var`` and
``chat_metrics_var`` — are installed at the top of a run (build thread entry
point or chat handler), incremented by the code paths that burn tokens /
write vectors / serialize graph payloads, and flushed onto the durable
artifact (``BuildJobRow.stats`` JSONB or ``ChatMessage.response`` JSONB) at
the end of the run.

Design notes
------------

* **ContextVars, not thread-locals.** ``BuildJob`` runs in a daemon
  ``threading.Thread`` spawned by the API layer; ``contextvars.ContextVar``
  values do not propagate across ``threading.Thread`` boundaries unless you
  ``copy_context().run(...)`` explicitly. We avoid that entirely by setting
  the var **inside** the build thread (and chat coroutine) at the top of the
  run. The result: the same module-level ``_llm_cache`` in ``src.agent.nodes``
  can be shared across concurrent builds + chats without cross-contamination.

* **Per-invocation callbacks, not cached on the client.** LLM token capture
  uses LangChain's ``config={"callbacks":[...]}`` at each ``invoke()`` site.
  This avoids mutating the shared cached ``ChatOpenAI`` client's ``callbacks``
  list (which would race across concurrent runs) and keeps the capture
  narrowly scoped. See ``llm_invoke``.

* **Double-counting.** ``on_llm_end`` fires once per *successful* underlying
  HTTP response. LangChain retries produce separate ``on_llm_end`` events —
  but only the final successful one reaches the call site, so we count
  ``llm_calls`` at the **call site** (in ``llm_invoke``) rather than in the
  callback. Token totals still come from the callback because the response
  object itself carries the usage payload.

* **Embeddings bypass LangChain.** The ``_OpenRouterEmbedder`` in
  ``src.graph_builder.embeddings`` uses the raw OpenAI SDK, so a LangChain
  callback catches zero embedding tokens. The embedding path reads
  ``response.usage.prompt_tokens`` directly and calls
  ``record_embedding_batch`` on this module — same accumulator, no callback.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Accumulators
# ---------------------------------------------------------------------------

@dataclass
class BuildMetrics:
    """Running totals for a single build. Flushed to BuildJobRow.stats at end."""
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_calls: int = 0
    embedding_prompt_tokens: int = 0
    embedding_vectors: int = 0
    embedding_dimension: int = 0
    graph_payload_bytes: int = 0
    stage_timings_ms: dict[str, int] = field(default_factory=dict)
    inputs_knowledge_files: int = 0
    inputs_tool_files: int = 0
    inputs_total_bytes: int = 0
    llm_model: Optional[str] = None
    embedding_model: Optional[str] = None
    build_started_perf: Optional[float] = None  # set by build_jobs on run start
    # Internal state used by ``mark_stage`` to record elapsed-since-previous.
    _current_stage_name: Optional[str] = None
    _current_stage_start_perf: Optional[float] = None

    def usage_dict(self) -> dict[str, int]:
        """Shape matches the `usage` sub-object documented in the plan."""
        return {
            "llm_prompt_tokens": self.llm_prompt_tokens,
            "llm_completion_tokens": self.llm_completion_tokens,
            "llm_total_tokens": self.llm_prompt_tokens + self.llm_completion_tokens,
            "llm_calls": self.llm_calls,
            "embedding_prompt_tokens": self.embedding_prompt_tokens,
            "embedding_vectors": self.embedding_vectors,
            "embedding_dimension": self.embedding_dimension,
            "graph_payload_bytes": self.graph_payload_bytes,
        }

    def inputs_dict(self) -> dict[str, int]:
        return {
            "knowledge_files": self.inputs_knowledge_files,
            "tool_files": self.inputs_tool_files,
            "total_bytes": self.inputs_total_bytes,
        }

    def models_dict(self) -> dict[str, Optional[str]]:
        return {"llm": self.llm_model, "embedding": self.embedding_model}


@dataclass
class ChatMetrics:
    """Running totals for a single chat turn. Flushed to QueryResponse.usage."""
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_calls: int = 0
    model: Optional[str] = None

    def usage_dict(self) -> dict[str, Any]:
        return {
            "llm_prompt_tokens": self.llm_prompt_tokens,
            "llm_completion_tokens": self.llm_completion_tokens,
            "llm_total_tokens": self.llm_prompt_tokens + self.llm_completion_tokens,
            "llm_calls": self.llm_calls,
            "model": self.model,
        }


build_metrics_var: contextvars.ContextVar[Optional[BuildMetrics]] = contextvars.ContextVar(
    "build_metrics", default=None
)
chat_metrics_var: contextvars.ContextVar[Optional[ChatMetrics]] = contextvars.ContextVar(
    "chat_metrics", default=None
)


def current_metrics() -> Optional[BuildMetrics | ChatMetrics]:
    """Return whichever metrics context is active (build takes precedence if both)."""
    m = build_metrics_var.get()
    if m is not None:
        return m
    return chat_metrics_var.get()


# ---------------------------------------------------------------------------
# LangChain callback — capture token usage on ``on_llm_end``
# ---------------------------------------------------------------------------

def _extract_token_usage(response: Any) -> tuple[int, int]:
    """Return (prompt_tokens, completion_tokens) from a LangChain ``LLMResult``.

    Handles three observed shapes:
      1. ``response.llm_output["token_usage"]`` — classic OpenAI path.
      2. ``response.generations[0][0].message.response_metadata["token_usage"]``
         — OpenRouter-via-langchain_openai commonly stashes usage here.
      3. ``response.generations[0][0].message.usage_metadata`` — langchain's
         normalized field on ``AIMessage`` for newer providers.
    Returns (0, 0) when no usage is present.
    """
    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") if isinstance(llm_output, dict) else None
    if isinstance(usage, dict):
        p = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        c = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        if p or c:
            return p, c

    try:
        gens = getattr(response, "generations", None) or []
        if gens and gens[0]:
            msg = getattr(gens[0][0], "message", None)
            if msg is not None:
                meta = getattr(msg, "response_metadata", {}) or {}
                meta_usage = meta.get("token_usage") if isinstance(meta, dict) else None
                if isinstance(meta_usage, dict):
                    p = int(meta_usage.get("prompt_tokens") or 0)
                    c = int(meta_usage.get("completion_tokens") or 0)
                    if p or c:
                        return p, c
                um = getattr(msg, "usage_metadata", None)
                if isinstance(um, dict):
                    p = int(um.get("input_tokens") or 0)
                    c = int(um.get("output_tokens") or 0)
                    if p or c:
                        return p, c
    except Exception:
        pass
    return 0, 0


def _make_callback_handler():
    """Return a LangChain BaseCallbackHandler that feeds the active accumulator.

    Deferred import because langchain_core may not be importable during
    offline / TF-IDF-only runs. The builder does not import this module at
    all in that path, so the import cost is paid only by callers who use it.
    """
    try:
        from langchain_core.callbacks import BaseCallbackHandler
    except Exception:  # pragma: no cover — optional dep
        return None

    class _TokenUsageHandler(BaseCallbackHandler):  # type: ignore[misc]
        def on_llm_end(self, response, **kwargs):  # type: ignore[override]
            try:
                p, c = _extract_token_usage(response)
                if not (p or c):
                    return
                m = current_metrics()
                if m is None:
                    return
                m.llm_prompt_tokens += p
                m.llm_completion_tokens += c
            except Exception as exc:
                logger.debug("token-usage capture failed: %s", exc)

    return _TokenUsageHandler()


# ---------------------------------------------------------------------------
# Call-site wrapper
# ---------------------------------------------------------------------------

def llm_invoke(llm, messages, **kwargs):
    """Drop-in replacement for ``llm.invoke(messages)`` that captures tokens.

    - When a metrics var is active: attaches a per-invocation callback so the
      underlying HTTP response's token usage is accumulated. ``llm_calls`` is
      bumped at the call site so retries inside langchain do not inflate it.
    - When no metrics var is active: passes through unchanged, zero overhead.
    """
    m = current_metrics()
    if m is None:
        return llm.invoke(messages, **kwargs)

    handler = _make_callback_handler()
    if handler is None:
        return llm.invoke(messages, **kwargs)

    config = dict(kwargs.pop("config", {}) or {})
    existing_cbs = list(config.get("callbacks") or [])
    existing_cbs.append(handler)
    config["callbacks"] = existing_cbs

    m.llm_calls += 1
    return llm.invoke(messages, config=config, **kwargs)


# ---------------------------------------------------------------------------
# Direct recording helpers for non-LangChain paths (embeddings, payload sizes)
# ---------------------------------------------------------------------------

def record_embedding_batch(prompt_tokens: int, vector_count: int, dimension: int) -> None:
    """Called by the OpenRouter embedding client after each batch."""
    m = build_metrics_var.get()
    if m is None:
        return
    try:
        m.embedding_prompt_tokens += int(prompt_tokens or 0)
        m.embedding_vectors += int(vector_count or 0)
        if dimension and not m.embedding_dimension:
            m.embedding_dimension = int(dimension)
    except Exception as exc:
        logger.debug("record_embedding_batch failed: %s", exc)


def record_graph_payload_bytes(byte_count: int) -> None:
    m = build_metrics_var.get()
    if m is None:
        return
    try:
        m.graph_payload_bytes = int(byte_count or 0)
    except Exception:
        pass


def measure_graph_payload_bytes(nodes, edges) -> int:
    """Approximate the JSON-serialised payload size of a node+edge set.

    This is a **payload-size proxy** — not Memgraph's on-disk or RSS usage.
    Included because actual graph-memory stats require a separate Memgraph
    round-trip which we'd rather not pay per build. If we later want a real
    number, add ``SHOW STORAGE INFO`` scraping in a follow-up.

    Accepts any iterables whose elements are either dataclasses or objects
    with a ``__dict__``; falls back to ``str()`` length for unknown types.
    """
    import dataclasses as _dc
    import json as _json

    def _size_of(obj) -> int:
        try:
            if _dc.is_dataclass(obj):
                return len(_json.dumps(_dc.asdict(obj), default=str))
            d = getattr(obj, "__dict__", None)
            if isinstance(d, dict):
                return len(_json.dumps(d, default=str))
            return len(str(obj))
        except Exception:
            return 0

    total = 0
    try:
        node_iter = nodes.values() if hasattr(nodes, "values") else nodes
        for n in node_iter:
            total += _size_of(n)
        for e in edges:
            total += _size_of(e)
    except Exception as exc:
        logger.debug("measure_graph_payload_bytes failed: %s", exc)
    return total


def record_stage_ms(stage_name: str, elapsed_ms: int) -> None:
    m = build_metrics_var.get()
    if m is None or not stage_name:
        return
    try:
        m.stage_timings_ms[stage_name] = int(elapsed_ms or 0)
    except Exception:
        pass


def mark_stage(stage_name: str) -> None:
    """Transition into ``stage_name``. Records elapsed time since the previous
    ``mark_stage`` call (if any) into ``stage_timings_ms[previous_stage]``.

    Call this at the start of each stage. Call ``finalize_stages`` after the
    last stage completes to record its elapsed time.
    """
    import time as _time
    m = build_metrics_var.get()
    if m is None:
        return
    now = _time.perf_counter()
    if m._current_stage_name and m._current_stage_start_perf is not None:
        m.stage_timings_ms[m._current_stage_name] = int(
            (now - m._current_stage_start_perf) * 1000
        )
    m._current_stage_name = stage_name
    m._current_stage_start_perf = now


def finalize_stages() -> None:
    """Close the in-flight stage (if any) and flush its elapsed time."""
    import time as _time
    m = build_metrics_var.get()
    if m is None:
        return
    if m._current_stage_name and m._current_stage_start_perf is not None:
        m.stage_timings_ms[m._current_stage_name] = int(
            (_time.perf_counter() - m._current_stage_start_perf) * 1000
        )
        m._current_stage_name = None
        m._current_stage_start_perf = None
