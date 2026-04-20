"""
LangGraph agent graph definition.

Graph topology:
  START -> classify_intent -> locate_entry_nodes -> traverse_graph
        -> build_steps -> resolve_tools -> synthesize_response -> END

With a conditional re-traversal loop if more context is needed.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from src.agent.nodes import (
    build_steps,
    classify_intent,
    locate_entry_nodes,
    resolve_tools,
    synthesize_response,
    traverse_graph,
)
from src.graph_builder.builder import KnowledgeGraph
from src.models.state import GraphAgentState

logger = logging.getLogger(__name__)

# Max re-traversal iterations to prevent infinite loops
_MAX_RETRAVERSAL = 2


def _make_node(fn, kg: KnowledgeGraph):
    """Wrap an agent node function to inject the KnowledgeGraph dependency."""
    def wrapped(state: GraphAgentState) -> dict[str, Any]:
        return fn(state, kg)
    wrapped.__name__ = fn.__name__
    return wrapped


def _should_retraverse(state: GraphAgentState) -> str:
    """Conditional edge: decide whether to go back and traverse more or continue."""
    if state.get("needs_more_context") and state.get("traversal_depth", 0) < _MAX_RETRAVERSAL:
        return "traverse_graph"
    return "build_steps"


def build_agent_graph(kg: KnowledgeGraph) -> Any:
    """
    Build and compile the LangGraph StateGraph.

    Args:
        kg: The loaded KnowledgeGraph instance.

    Returns:
        A compiled LangGraph runnable.
    """
    graph = StateGraph(GraphAgentState)

    # Register nodes
    graph.add_node("classify_intent", _make_node(classify_intent, kg))
    graph.add_node("locate_entry_nodes", _make_node(locate_entry_nodes, kg))
    graph.add_node("traverse_graph", _make_node(traverse_graph, kg))
    graph.add_node("build_steps", _make_node(build_steps, kg))
    graph.add_node("resolve_tools", _make_node(resolve_tools, kg))
    graph.add_node("synthesize_response", _make_node(synthesize_response, kg))

    # Edges
    graph.add_edge(START, "classify_intent")
    graph.add_edge("classify_intent", "locate_entry_nodes")
    graph.add_edge("locate_entry_nodes", "traverse_graph")

    # Conditional re-traversal loop
    graph.add_conditional_edges(
        "traverse_graph",
        _should_retraverse,
        {
            "traverse_graph": "traverse_graph",
            "build_steps": "build_steps",
        },
    )

    graph.add_edge("build_steps", "resolve_tools")
    graph.add_edge("resolve_tools", "synthesize_response")
    graph.add_edge("synthesize_response", END)

    compiled = graph.compile()
    logger.info("LangGraph agent compiled successfully.")
    return compiled


class KBGraphAgent:
    """
    High-level agent wrapper.

    Usage:
        agent = KBGraphAgent.from_graph(kg)
        result = agent.query("How do I onboard a new client?")
        print(result["response"])
    """

    def __init__(self, runnable: Any, kg: KnowledgeGraph) -> None:
        self._runnable = runnable
        self._kg = kg

    @classmethod
    def from_graph(cls, kg: KnowledgeGraph) -> "KBGraphAgent":
        runnable = build_agent_graph(kg)
        return cls(runnable, kg)

    def _initial_state(self, query: str, llm_model: str | None) -> GraphAgentState:
        return {
            "query": query,
            "intent": "",
            "extracted_topics": [],
            "kb_focus": "both",
            "entry_nodes": [],
            "traversal_path": [],
            "visited_node_ids": [],
            "gathered_context": [],
            "steps": [],
            "tools_referenced": [],
            "knowledge_concepts": [],
            "response": "",
            "follow_up_suggestions": [],
            "traversal_depth": 0,
            "needs_more_context": False,
            "error": None,
            "llm_model": llm_model,
        }

    def query(self, query: str, llm_model: str | None = None) -> GraphAgentState:
        """
        Run the agent on a user query.

        Args:
            query: The user's natural-language query.
            llm_model: OpenRouter model id to use for this run (e.g.
                ``"anthropic/claude-3-7-sonnet"``). When None, nodes fall
                back to the env ``LLM_MODEL`` default.

        Returns the full final state: response (markdown), steps, tools,
        follow-ups.
        """
        logger.info(f"Running agent query: {query[:80]} (llm={llm_model or 'default'})")
        final_state: GraphAgentState = self._runnable.invoke(
            self._initial_state(query, llm_model)
        )
        return final_state

    def stream(self, query: str, llm_model: str | None = None):
        """Stream intermediate states for progressive UI updates."""
        yield from self._runnable.stream(self._initial_state(query, llm_model))
