"""
LangGraph agent node implementations.

Each function takes the current GraphAgentState and returns a partial state update.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_openai import ChatOpenAI

from src.agent.prompts import DomainPrompts, format_context_for_llm, get_domain_prompts
from src.config import (
    LLM_MAX_TOKENS,
    LLM_MODEL,
    LLM_TEMPERATURE,
    MAX_TRAVERSAL_DEPTH,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    TOP_K_ENTRY_NODES,
)


def _p() -> DomainPrompts:
    """Lazy accessor for the active domain prompts."""
    return get_domain_prompts()


from src.graph_builder.builder import KnowledgeGraph
from src.models.nodes import (
    ChapterNode,
    EdgeType,
    GlossaryNode,
    KBSource,
    NodeType,
    ProcessNode,
    SectionNode,
    TableNode,
    ToolNode,
)
from src.models.state import GraphAgentState

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-model LLM cache
# ---------------------------------------------------------------------------
# Each distinct model string gets its own ChatOpenAI client. Building a client
# is cheap (no network call at construction), so this dict is effectively a
# free amortization — callers still pay zero cost on the happy path, but we
# don't rebuild a client on every request for the same model.

_llm_cache: dict[str, ChatOpenAI] = {}


def _get_llm(model: str | None = None) -> ChatOpenAI:
    """Return a ChatOpenAI client for *model* (falls back to env LLM_MODEL).

    Clients are cached per resolved model name so a single workspace that
    pins e.g. 'anthropic/claude-3-7-sonnet' doesn't construct a new client
    per request.
    """
    resolved = (model or LLM_MODEL).strip() or LLM_MODEL
    client = _llm_cache.get(resolved)
    if client is None:
        client = ChatOpenAI(
            model=resolved,
            openai_api_base=OPENROUTER_BASE_URL,
            openai_api_key=OPENROUTER_API_KEY,
            temperature=LLM_TEMPERATURE,
            max_tokens=LLM_MAX_TOKENS,
        )
        _llm_cache[resolved] = client
    return client


# ---------------------------------------------------------------------------
# Node 1: classify_intent
# ---------------------------------------------------------------------------

def classify_intent(state: GraphAgentState, kg: KnowledgeGraph) -> dict[str, Any]:
    """
    Classify the user's query into intent + topics + kb_focus.
    Uses Qwen LLM for classification.
    """
    query = state["query"]
    llm = _get_llm(state.get("llm_model"))

    prompt = _p().intent_classification_template.format(query=query)
    messages = [
        {"role": "system", "content": _p().intent_classification_system},
        {"role": "user", "content": prompt},
    ]

    try:
        from src.observability.usage import llm_invoke
        result = llm_invoke(llm, messages)
        text = result.content if hasattr(result, "content") else str(result)

        # Strip thinking tags if present (some Qwen models include <think>...</think>)
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        # Extract JSON
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            return {
                "intent": parsed.get("intent", "explore"),
                "extracted_topics": parsed.get("extracted_topics", []),
                "kb_focus": parsed.get("kb_focus", "both"),
                "traversal_depth": 0,
                "needs_more_context": False,
                "error": None,
            }
    except Exception as exc:
        logger.error(f"Intent classification failed: {exc}")

    # Fallback: simple keyword heuristics
    q_lower = query.lower()
    if any(w in q_lower for w in ["how to", "steps", "process", "workflow", "procedure", "onboard"]):
        intent = "process"
    elif any(w in q_lower for w in ["what tool", "which system", "software", "platform", "crm", "database"]):
        intent = "tool_lookup"
    elif any(w in q_lower for w in ["compare", "difference", "versus", "vs", "better"]):
        intent = "compare"
    else:
        intent = "explore"

    kb_focus = "tool" if intent == "tool_lookup" else "both"

    return {
        "intent": intent,
        "extracted_topics": query.split()[:5],
        "kb_focus": kb_focus,
        "traversal_depth": 0,
        "needs_more_context": False,
        "error": None,
    }


# ---------------------------------------------------------------------------
# Node 2: locate_entry_nodes
# ---------------------------------------------------------------------------

def locate_entry_nodes(state: GraphAgentState, kg: KnowledgeGraph) -> dict[str, Any]:
    """
    Find the best starting nodes in the graph for this query.
    Uses hybrid search (FAISS + keyword) with intent-aware filtering.
    """
    query = state["query"]
    topics = state.get("extracted_topics", [])
    kb_focus = state.get("kb_focus", "both")
    intent = state.get("intent", "explore")

    # Build augmented query from topics
    augmented_query = " ".join([query] + topics)

    # Semantic + keyword hybrid search
    candidates = kg.hybrid_search(augmented_query, top_k=TOP_K_ENTRY_NODES * 3)

    # Filter by kb_focus. ``hybrid_search`` can return ids that exist in the
    # vector store but not in the in-memory ``kg.nodes`` dict (stale Qdrant
    # vectors from a prior build, or a row that was filtered out of the
    # extractor after its embedding was computed). Guard with ``.get()`` or
    # the whole chat turn crashes with a bare ``KeyError('tool:ch15:...')``
    # that renders as "Error: <node_id>" to the user.
    def _kb_source_is(nid: str, src: KBSource) -> bool:
        node = kg.nodes.get(nid)
        return node is not None and node.kb_source == src

    if kb_focus == "knowledge":
        candidates = [(nid, s) for nid, s in candidates
                      if _kb_source_is(nid, KBSource.KNOWLEDGE)]
    elif kb_focus == "tool":
        candidates = [(nid, s) for nid, s in candidates
                      if _kb_source_is(nid, KBSource.TOOL)]

    # Boost ChapterNode and SectionNode for explore/process intents
    boosted: list[tuple[str, float]] = []
    for nid, score in candidates:
        node = kg.nodes.get(nid)
        if not node:
            continue
        boost = 0.0
        if intent in ("explore", "process") and node.node_type in (NodeType.CHAPTER, NodeType.SECTION):
            boost = 0.15
        if intent == "tool_lookup" and node.node_type == NodeType.TOOL:
            boost = 0.20
        if intent == "process" and node.node_type == NodeType.PROCESS:
            boost = 0.25
        boosted.append((nid, score + boost))

    boosted.sort(key=lambda x: x[1], reverse=True)
    entry_nodes = [nid for nid, _ in boosted[:TOP_K_ENTRY_NODES]]

    logger.info(f"Located {len(entry_nodes)} entry nodes for query: {query[:60]}")
    return {
        "entry_nodes": entry_nodes,
        "traversal_path": [],
        "visited_node_ids": [],
        "gathered_context": [],
    }


# ---------------------------------------------------------------------------
# Node 3: traverse_graph
# ---------------------------------------------------------------------------

def traverse_graph(state: GraphAgentState, kg: KnowledgeGraph) -> dict[str, Any]:
    """
    BFS traversal from entry nodes, collecting context.
    Adapts traversal strategy based on intent.
    """
    intent = state.get("intent", "explore")
    entry_nodes = state.get("entry_nodes", [])
    traversal_depth = state.get("traversal_depth", 0)

    # Determine which edge types to follow
    if intent == "process":
        edge_types = [
            EdgeType.CONTAINS.value,
            EdgeType.HAS_CONTENT.value,
            EdgeType.NEXT_STEP.value,
            EdgeType.USES_TOOL.value,
        ]
        max_depth = min(MAX_TRAVERSAL_DEPTH, 4)
        max_nodes = 50
    elif intent == "tool_lookup":
        edge_types = [
            EdgeType.CONTAINS.value,
            EdgeType.INTEGRATES_WITH.value,
            EdgeType.IMPLEMENTS.value,
        ]
        max_depth = 3
        max_nodes = 40
    elif intent == "compare":
        edge_types = [
            EdgeType.CONTAINS.value,
            EdgeType.RELATED_TO.value,
            EdgeType.IMPLEMENTS.value,
        ]
        max_depth = 3
        max_nodes = 40
    else:  # explore
        edge_types = [
            EdgeType.CONTAINS.value,
            EdgeType.HAS_CONTENT.value,
            EdgeType.RELATED_TO.value,
        ]
        max_depth = 3
        max_nodes = 35

    traversal_path = kg.bfs_traverse(
        entry_nodes,
        max_depth=max_depth,
        edge_types=edge_types,
        max_nodes=max_nodes,
    )

    # Build gathered_context from traversal path
    gathered_context: list[dict[str, Any]] = []
    for nid in traversal_path:
        node = kg.nodes.get(nid)
        if not node:
            continue
        ctx = _node_to_context_item(node, kg)
        gathered_context.append(ctx)

    return {
        "traversal_path": traversal_path,
        "visited_node_ids": list(set(traversal_path)),
        "gathered_context": gathered_context,
        "traversal_depth": traversal_depth + 1,
        "needs_more_context": False,
    }


def _node_to_context_item(node: Any, kg: KnowledgeGraph) -> dict[str, Any]:
    """Convert a node to a context item dict for the agent state."""
    base = {
        "node_id": node.node_id,
        "node_type": node.node_type.value,
        "heading": node.heading,
        "content": node.content_summary or node.raw_text[:500],
        "kb_source": node.kb_source.value,
        "tools": [],
        "steps": [],
    }

    if isinstance(node, SectionNode):
        base["content"] = "\n".join(node.paragraphs[:3]) if node.paragraphs else node.content_summary

    elif isinstance(node, ToolNode):
        base["tools"] = [node.to_dict()]
        base["content"] = node.purpose or node.content_summary

    elif isinstance(node, ProcessNode):
        base["steps"] = [{
            "step_number": node.step_number,
            "step_name": node.step_name,
            "system_used": node.system_used,
            "required_actions": node.required_actions,
            "time_to_complete": node.time_to_complete,
            "compliance_checks": node.compliance_checks,
        }]

    elif isinstance(node, TableNode):
        base["content"] = f"{node.caption or 'Table'} — Columns: {', '.join(node.headers[:8])}"

    elif isinstance(node, GlossaryNode):
        base["content"] = f"{node.term}: {node.definition}"

    return base


# ---------------------------------------------------------------------------
# Node 4: build_steps
# ---------------------------------------------------------------------------

def build_steps(state: GraphAgentState, kg: KnowledgeGraph) -> dict[str, Any]:
    """
    Organise the traversal context into structured steps.
    For process intent: order by NEXT_STEP edges.
    For explore/compare: group by chapter/section hierarchy.
    """
    intent = state.get("intent", "explore")
    traversal_path = state.get("traversal_path", [])
    gathered_context = state.get("gathered_context", [])

    # --- Process: ordered steps ---
    if intent == "process":
        steps = _build_process_steps(traversal_path, kg)
    else:
        steps = _build_outline_steps(gathered_context)

    # Collect all tools referenced across all steps
    tools_referenced: list[dict[str, Any]] = []
    knowledge_concepts: list[dict[str, Any]] = []
    seen_tools: set[str] = set()
    seen_concepts: set[str] = set()

    for nid in traversal_path:
        node = kg.nodes.get(nid)
        if isinstance(node, ToolNode) and node.node_id not in seen_tools:
            seen_tools.add(node.node_id)
            tools_referenced.append(node.to_dict())
        if isinstance(node, GlossaryNode) and node.node_id not in seen_concepts:
            seen_concepts.add(node.node_id)
            knowledge_concepts.append({"term": node.term, "definition": node.definition})

    return {
        "steps": steps,
        "tools_referenced": tools_referenced,
        "knowledge_concepts": knowledge_concepts,
    }


def _build_process_steps(traversal_path: list[str], kg: KnowledgeGraph) -> list[dict]:
    """Build ordered process steps following NEXT_STEP edges."""
    # Find ProcessNodes in traversal, ordered by step_number
    process_nodes = [
        kg.nodes[nid]
        for nid in traversal_path
        if isinstance(kg.nodes.get(nid), ProcessNode)
    ]
    process_nodes.sort(key=lambda n: n.step_number)  # type: ignore[attr-defined]

    steps = []
    for pn in process_nodes:
        # Find tools used in this step
        step_tools = []
        if pn.system_used:
            # Look up tools by name match
            search_results = kg.keyword_search(pn.system_used, top_k=3,
                                               node_types=[NodeType.TOOL])
            for tool_id, _ in search_results:
                tool_node = kg.nodes.get(tool_id)
                if tool_node:
                    step_tools.append(tool_node.to_dict())

        steps.append({
            "step_number": pn.step_number,
            "title": pn.step_name,
            "description": pn.required_actions or pn.content_summary,
            "node_id": pn.node_id,
            "node_type": "process",
            "tools": step_tools,
            "related_concepts": [],
            "system_used": pn.system_used,
            "time_to_complete": pn.time_to_complete,
            "compliance_checks": pn.compliance_checks,
        })

    return steps


def _build_outline_steps(gathered_context: list[dict]) -> list[dict]:
    """Build outline-style steps from gathered context for explore/compare intents."""
    steps = []
    step_num = 1
    for item in gathered_context:
        if item["node_type"] in ("chapter", "section"):
            steps.append({
                "step_number": step_num,
                "title": item["heading"],
                "description": item["content"],
                "node_id": item["node_id"],
                "node_type": item["node_type"],
                "tools": item.get("tools", []),
                "related_concepts": [],
            })
            step_num += 1
        elif item["node_type"] == "tool":
            # Merge tool info into nearest step or create new
            if steps:
                steps[-1]["tools"].append(item["tools"][0] if item["tools"] else {})
    return steps


# ---------------------------------------------------------------------------
# Node 5: resolve_tools
# ---------------------------------------------------------------------------

def resolve_tools(state: GraphAgentState, kg: KnowledgeGraph) -> dict[str, Any]:
    """
    Enrich each step with fully resolved tool information.
    Also ensures tools_referenced has complete schema data.
    """
    steps = state.get("steps", [])
    traversal_path = state.get("traversal_path", [])
    tools_referenced = state.get("tools_referenced", [])

    # For each entry node, pull tool nodes from the graph
    entry_nodes = state.get("entry_nodes", [])
    for nid in entry_nodes:
        tool_nodes = kg.get_tools_for_node(nid)
        seen_ids = {t.get("node_id") for t in tools_referenced if isinstance(t, dict)}
        for tn in tool_nodes:
            if tn.node_id not in seen_ids:
                tools_referenced.append(tn.to_dict())
                seen_ids.add(tn.node_id)

    # Enrich steps with tool details
    for step in steps:
        if not step.get("tools") and step.get("node_id"):
            node_tools = kg.get_tools_for_node(step["node_id"])
            step["tools"] = [t.to_dict() for t in node_tools[:3]]

    return {
        "steps": steps,
        "tools_referenced": tools_referenced,
    }


# ---------------------------------------------------------------------------
# Node 6: synthesize_response
# ---------------------------------------------------------------------------

def synthesize_response(state: GraphAgentState, kg: KnowledgeGraph) -> dict[str, Any]:
    """
    Use the LLM to synthesize the final response from all gathered context.
    """
    query = state["query"]
    intent = state.get("intent", "explore")
    gathered_context = state.get("gathered_context", [])
    steps = state.get("steps", [])
    tools_referenced = state.get("tools_referenced", [])
    knowledge_concepts = state.get("knowledge_concepts", [])

    # Build context for LLM
    context_parts: list[str] = []

    if steps:
        context_parts.append("## Structured Steps Found:")
        for step in steps:
            context_parts.append(
                f"Step {step.get('step_number', '?')}: {step.get('title', '')}\n"
                f"  Description: {step.get('description', '')[:300]}\n"
                f"  System: {step.get('system_used', '') or step.get('tools', [{}])[0].get('tool_name', '') if step.get('tools') else 'N/A'}"
            )

    if tools_referenced:
        context_parts.append("\n## Tools & Systems Found:")
        for tool in tools_referenced[:15]:
            tool_name = tool.get("tool_name") or tool.get("heading", "")
            provider = tool.get("provider", "")
            purpose = tool.get("purpose", "")[:200]
            connected = ", ".join(tool.get("connected_systems", [])[:3])
            sla = tool.get("sla", "")
            context_parts.append(
                f"- **{tool_name}** ({provider}): {purpose}"
                + (f"\n  Connected: {connected}" if connected else "")
                + (f" | SLA: {sla}" if sla else "")
            )

    if knowledge_concepts:
        context_parts.append("\n## Relevant Glossary Terms:")
        for concept in knowledge_concepts[:8]:
            context_parts.append(f"- **{concept['term']}**: {concept['definition'][:200]}")

    # Include section content from gathered_context
    section_contexts = [
        item for item in gathered_context
        if item["node_type"] in ("section", "chapter") and item.get("content")
    ][:8]
    if section_contexts:
        context_parts.append("\n## Knowledge Base Sections:")
        for sc in section_contexts:
            context_parts.append(f"### {sc['heading']}\n{sc['content'][:400]}")

    context_text = "\n".join(context_parts)

    # Construct prompt
    template = _p().get_synthesis_template(intent)
    user_prompt = template.format(query=query, context=context_text)

    llm = _get_llm(state.get("llm_model"))
    messages = [
        {"role": "system", "content": _p().synthesize_system},
        {"role": "user", "content": user_prompt},
    ]

    try:
        from src.observability.usage import llm_invoke
        result = llm_invoke(llm, messages)
        response_text = result.content if hasattr(result, "content") else str(result)
        # Strip thinking tags
        response_text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL).strip()
    except Exception as exc:
        logger.error(f"Response synthesis failed: {exc}")
        response_text = _fallback_response(state)

    # Generate follow-up suggestions
    follow_ups = _generate_follow_ups(state)

    return {
        "response": response_text,
        "follow_up_suggestions": follow_ups,
    }


def _fallback_response(state: GraphAgentState) -> str:
    """Generate a basic response when LLM synthesis fails."""
    steps = state.get("steps", [])
    tools = state.get("tools_referenced", [])

    lines = [f"# Response to: {state['query']}\n"]
    if steps:
        lines.append("## Steps Found:")
        for s in steps[:10]:
            lines.append(f"- Step {s.get('step_number', '?')}: {s.get('title', '')}")
    if tools:
        lines.append("\n## Tools Referenced:")
        for t in tools[:10]:
            lines.append(f"- {t.get('tool_name', t.get('heading', 'Unknown'))}: {t.get('purpose', '')[:100]}")
    return "\n".join(lines)


def _generate_follow_ups(state: GraphAgentState) -> list[str]:
    """Generate follow-up question suggestions based on traversal."""
    intent = state.get("intent", "explore")
    topics = state.get("extracted_topics", [])
    tools = state.get("tools_referenced", [])

    suggestions: list[str] = []
    topic = topics[0] if topics else "this topic"

    if intent == "explore":
        suggestions = [
            f"What tools at {_p().organization_name} support {topic}?",
            f"What are the regulatory requirements for {topic}?",
            f"What are the step-by-step processes for implementing {topic}?",
        ]
    elif intent == "process":
        suggestions = [
            f"What compliance checks are required in this process?",
            f"How do the systems integrate in this workflow?",
            f"What are the exception handling procedures for this process?",
        ]
    elif intent == "tool_lookup":
        tool_name = tools[0].get("tool_name", "this tool") if tools else "this tool"
        suggestions = [
            f"How does {tool_name} integrate with the portfolio management system?",
            f"What are the security and access controls for {tool_name}?",
            f"What processes use {tool_name}?",
        ]
    elif intent == "compare":
        suggestions = [
            f"What are the cost implications of each option?",
            f"What does the implementation timeline look like?",
            f"What regulatory considerations apply?",
        ]

    return suggestions
