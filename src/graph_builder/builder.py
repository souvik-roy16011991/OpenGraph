"""
Graph builder orchestrator.

Pipeline:
  1. Parse both KB JSON files -> ParsedKB objects
  2. Extract nodes -> dict[node_id, BaseNode]
  3. Build structural + cross-KB edges
  4. Generate embeddings + vector store (Pinecone or FAISS) + RELATED_TO edges
  5. Persist graph: Neo4j (cloud) or NetworkX pickle (local)

Also provides KnowledgeGraph – the runtime graph object loaded by the agent.
At runtime, each method checks self._neo4j first; if present it delegates to
the Neo4j store, otherwise it falls back to the local NetworkX graph.
"""

from __future__ import annotations

import json
import logging
import pickle
from pathlib import Path
from typing import Any

import networkx as nx

from src.config import (
    EMBEDDING_DIM,
    FAISS_INDEX_PATH,
    GRAPH_PICKLE_PATH,
    MEMGRAPH_DATABASE,
    MEMGRAPH_PASSWORD,
    MEMGRAPH_URI,
    MEMGRAPH_USERNAME,
    NEO4J_DATABASE,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USERNAME,
    NODE_REGISTRY_PATH,
    PINECONE_API_KEY,
    PINECONE_INDEX_NAME,
    PINECONE_NAMESPACE,
    QDRANT_API_KEY,
    QDRANT_COLLECTION_NAME,
    QDRANT_URL,
    TOP_K_ENTRY_NODES,
    USE_MEMGRAPH,
    USE_NEO4J,
    USE_PINECONE,
    USE_QDRANT,
)
from src.config import workspace_paths
from src.graph_config import get_graph_config
from src.kb_config import get_active_kb_config
from src.graph_builder.edges import build_edges
from src.graph_builder.embeddings import EmbeddingPipeline, run_embedding_pipeline, semantic_search
from src.graph_builder.extractor import extract_all_nodes
from src.graph_builder.parser import parse_kb_file, parse_kb_files
from src.workspace_context import get_current_workspace
from src.models.nodes import (
    BaseNode,
    ChapterNode,
    Edge,
    EdgeType,
    GlossaryNode,
    KBSource,
    NodeType,
    SectionNode,
    TableNode,
    ToolNode,
    node_from_dict,
)

logger = logging.getLogger(__name__)


def _load_embed_model(faiss_path: Path, nodes: dict) -> Any:
    """
    Load the embedding model that was used at build time.
    Checks the model_type marker file to decide which model to load.
    """
    from src.graph_builder.embeddings import _TFIDFEmbedder, _node_text

    model_type_path = faiss_path.with_suffix(".model_type.txt")
    model_type = "sentence_transformers"
    if model_type_path.exists():
        model_type = model_type_path.read_text().strip()

    if model_type == "tfidf":
        tfidf_path = faiss_path.with_suffix(".tfidf.pkl")
        if tfidf_path.exists():
            logger.info("Loading saved TF-IDF embedding model…")
            with open(tfidf_path, "rb") as f:
                import pickle as _pkl
                return _pkl.load(f)
        else:
            logger.info("Reconstructing TF-IDF embedding model from nodes…")
            model = _TFIDFEmbedder()
            texts = [_node_text(n) for n in nodes.values()]
            model.fit(texts)
            return model
    elif model_type == "openrouter":
        from src.graph_builder.embeddings import _OpenRouterEmbedder
        from src.config import EMBEDDING_DIMENSIONS, EMBEDDING_MODEL, OPENROUTER_API_KEY, OPENROUTER_BASE_URL
        logger.info(f"Loading OpenRouter embedding model: {EMBEDDING_MODEL}")
        return _OpenRouterEmbedder(
            EMBEDDING_MODEL, OPENROUTER_API_KEY, OPENROUTER_BASE_URL, EMBEDDING_DIMENSIONS
        )
    else:
        from src.config import EMBEDDING_MODEL
        for local_only in (True, False):
            try:
                from sentence_transformers import SentenceTransformer
                logger.info(f"Loading sentence-transformer model (local_files_only={local_only})…")
                return SentenceTransformer(EMBEDDING_MODEL, local_files_only=local_only)
            except Exception as exc:
                if local_only:
                    logger.debug(f"Local-only load failed: {exc}")
                    continue
                logger.warning(f"sentence-transformers failed ({exc}), using TF-IDF fallback")
        model = _TFIDFEmbedder()
        texts = [_node_text(n) for n in nodes.values()]
        model.fit(texts)
        return model


# ---------------------------------------------------------------------------
# Build pipeline
# ---------------------------------------------------------------------------

def _short_wid(wid: str) -> str:
    """Qdrant-safe short form of a workspace UUID for collection naming."""
    return wid.replace("-", "")[:12]


def build_graph(
    use_llm_cross_links: bool = True,
    skip_embeddings: bool = False,
    workspace_id: str | None = None,
    knowledge_paths: list[Path] | None = None,
    tool_paths: list[Path] | None = None,
) -> "KnowledgeGraph":
    """
    Full build pipeline for a workspace. Returns a KnowledgeGraph ready for queries.

    Args:
        use_llm_cross_links: Whether to call the LLM for cross-KB IMPLEMENTS edges.
        skip_embeddings: Set True to skip embedding generation (faster during dev).
        workspace_id: REQUIRED — every artifact (Memgraph nodes, Qdrant collection,
            local cache, Neon rows) is scoped to this id.
        knowledge_paths, tool_paths: lists of local JSON files to merge into a
            single ParsedKB per kb_source. If None, falls back to
            get_active_kb_config() single-file paths (legacy CLI mode).
    """
    if workspace_id is None:
        workspace_id = get_current_workspace()
    if not workspace_id:
        raise ValueError(
            "build_graph requires workspace_id (explicit arg or via workspace_context)."
        )

    paths = workspace_paths(workspace_id)
    qdrant_collection = f"kb-{_short_wid(workspace_id)}"

    logger.info("=== KB Knowledge Graph Build Pipeline (ws=%s) ===", workspace_id)
    if USE_MEMGRAPH:
        logger.info("  Graph backend: Memgraph (workspace-scoped)")
    elif USE_NEO4J:
        logger.info("  Graph backend: Neo4j (workspace-scoped)")
    else:
        logger.info("  Graph backend: NetworkX (local cache only)")
    if USE_QDRANT:
        logger.info("  Vector backend: Qdrant Cloud — collection %s", qdrant_collection)
    elif USE_PINECONE:
        logger.info("  Vector backend: Pinecone")
    else:
        logger.info("  Vector backend: FAISS (local)")

    # 1. Parse — multi-file aware
    logger.info("Step 1/5 – Parsing KB files…")
    if knowledge_paths is None or tool_paths is None:
        cfg = get_active_kb_config()
        if knowledge_paths is None:
            knowledge_paths = [cfg.knowledge_kb_path]
        if tool_paths is None:
            tool_paths = [cfg.tool_kb_path]

    knowledge_kb = parse_kb_files(knowledge_paths, "knowledge")
    tool_kb = parse_kb_files(tool_paths, "tool")
    logger.info(
        "  Knowledge KB: %d chapters across %d file(s) | Tool KB: %d chapters across %d file(s)",
        len(knowledge_kb.chapters), len(knowledge_paths),
        len(tool_kb.chapters), len(tool_paths),
    )

    # 2. Extract nodes (stamped with workspace_id so every node carries tenancy)
    logger.info("Step 2/5 – Extracting nodes…")
    nodes = extract_all_nodes(knowledge_kb, tool_kb, workspace_id=workspace_id)
    _log_node_counts(nodes)

    # 3. Build edges
    logger.info("Step 3/5 – Building edges…")
    edges = build_edges(nodes, use_llm_cross_links=use_llm_cross_links)
    _log_edge_counts(edges)

    # 4. Embeddings + semantic edges
    pinecone_store = None
    faiss_index = None

    if not skip_embeddings:
        logger.info("Step 4/5 – Generating embeddings…")
        embeddings, related_edges, vector_store = run_embedding_pipeline(
            nodes, workspace_id=workspace_id, qdrant_collection=qdrant_collection,
        )
        edges.extend(related_edges)
        logger.info(f"  Total edges after RELATED_TO: {len(edges)}")

        if USE_PINECONE:
            pinecone_store = vector_store
        else:
            faiss_index = vector_store
    else:
        logger.info("Step 4/5 – Skipping embeddings (skip_embeddings=True)")

    # 5. Persist graph (per-workspace)
    neo4j_store = None
    G: nx.DiGraph | None = None

    if USE_MEMGRAPH:
        logger.info("Step 5/5 – Writing graph to Memgraph (ws=%s)…", workspace_id)
        try:
            from src.infra.memgraph_store import MemgraphGraphStore
            neo4j_store = MemgraphGraphStore(
                MEMGRAPH_URI, MEMGRAPH_USERNAME, MEMGRAPH_PASSWORD, MEMGRAPH_DATABASE
            )
            neo4j_store.clear_workspace(workspace_id)
            neo4j_store.bulk_create_nodes_no_apoc(nodes, workspace_id=workspace_id)
            neo4j_store.bulk_create_edges(edges, workspace_id=workspace_id)
            ws_stats = neo4j_store.stats(workspace_id=workspace_id)
            logger.info(
                "  Memgraph ws=%s: %d nodes, %d edges",
                workspace_id, ws_stats["total_nodes"], ws_stats["total_edges"],
            )
        except Exception as exc:
            logger.error(
                "Memgraph write failed (%s). Continuing with local NetworkX fallback.", exc
            )
            neo4j_store = None

        G = _build_networkx_graph(nodes, edges)
        logger.info("  NetworkX cache: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())
        _save_graph(G, nodes, workspace_id=workspace_id)
    elif USE_NEO4J:
        logger.info("Step 5/5 – Writing graph to Neo4j (ws=%s)…", workspace_id)
        try:
            from src.infra.neo4j_store import Neo4jGraphStore
            neo4j_store = Neo4jGraphStore(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE)
            neo4j_store.clear_workspace(workspace_id)
            neo4j_store.bulk_create_nodes_no_apoc(nodes, workspace_id=workspace_id)
            neo4j_store.bulk_create_edges(edges, workspace_id=workspace_id)
            ws_stats = neo4j_store.stats(workspace_id=workspace_id)
            logger.info("  Neo4j ws=%s: %d nodes, %d edges",
                        workspace_id, ws_stats["total_nodes"], ws_stats["total_edges"])
        except Exception as exc:
            logger.error(
                "Neo4j write failed (%s). Continuing with local NetworkX fallback.", exc
            )
            neo4j_store = None

        # Always build a local NetworkX graph as a fast runtime cache
        G = _build_networkx_graph(nodes, edges)
        logger.info("  NetworkX cache: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())
        _save_graph(G, nodes, workspace_id=workspace_id)
    else:
        logger.info("Step 5/5 – Populating NetworkX DiGraph (ws=%s)…", workspace_id)
        G = _build_networkx_graph(nodes, edges)
        logger.info("  Graph: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())
        _save_graph(G, nodes, workspace_id=workspace_id)

    kg = KnowledgeGraph(
        G=G,
        nodes=nodes,
        workspace_id=workspace_id,
        faiss_index=faiss_index,
        neo4j_store=neo4j_store,
        pinecone_store=pinecone_store,
    )
    logger.info("=== Build complete (ws=%s) ===", workspace_id)
    return kg


def _log_node_counts(nodes: dict[str, BaseNode]) -> None:
    counts: dict[str, int] = {}
    for n in nodes.values():
        counts[n.node_type.value] = counts.get(n.node_type.value, 0) + 1
    logger.info("  Node counts: " + " | ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def _log_edge_counts(edges: list[Edge]) -> None:
    counts: dict[str, int] = {}
    for e in edges:
        counts[e.edge_type.value] = counts.get(e.edge_type.value, 0) + 1
    logger.info("  Edge counts: " + " | ".join(f"{k}={v}" for k, v in sorted(counts.items())))


def _build_networkx_graph(nodes: dict[str, BaseNode], edges: list[Edge]) -> nx.DiGraph:
    G = nx.DiGraph()
    for nid, node in nodes.items():
        G.add_node(nid, **node.to_dict())
    for edge in edges:
        if edge.source_id in G and edge.target_id in G:
            G.add_edge(
                edge.source_id,
                edge.target_id,
                edge_type=edge.edge_type.value,
                weight=edge.weight,
                **edge.metadata,
            )
    return G


def _save_graph(G: nx.DiGraph, nodes: dict[str, BaseNode], workspace_id: str) -> None:
    paths = workspace_paths(workspace_id)
    path = paths["graph_pickle"]
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "wb") as f:
        pickle.dump(G, f, protocol=pickle.HIGHEST_PROTOCOL)
    logger.info("  Graph saved to %s", path)

    registry = {nid: node.to_dict() for nid, node in nodes.items()}
    reg_path = paths["node_registry"]
    with open(reg_path, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False)
    logger.info("  Node registry saved to %s", reg_path)


# ---------------------------------------------------------------------------
# KnowledgeGraph – runtime object
# ---------------------------------------------------------------------------

class KnowledgeGraph:
    """
    The loaded, in-memory knowledge graph.

    Supports two parallel code paths for every operation:
      - Cloud path  : delegates to self._neo4j (Neo4jGraphStore) and
                      self._pinecone (PineconeVectorStore) when set.
      - Local path  : uses self.G (NetworkX DiGraph) and
                      self._faiss_index + self._embed_model.

    At runtime the local NetworkX graph is always loaded (as a fast cache),
    so most traversal calls hit NetworkX regardless of which backend was used
    during build.  Only semantic_search uses Pinecone at runtime when available.
    """

    def __init__(
        self,
        G: nx.DiGraph | None,
        nodes: dict[str, BaseNode],
        workspace_id: str,
        faiss_index: Any = None,
        faiss_node_ids: list[str] | None = None,
        neo4j_store: Any = None,
        pinecone_store: Any = None,
    ) -> None:
        self.G: nx.DiGraph = G if G is not None else nx.DiGraph()
        self.nodes = nodes
        self.workspace_id = workspace_id
        self._faiss_index = faiss_index
        self._faiss_node_ids: list[str] = faiss_node_ids or []
        self._embed_model: Any = None
        self._neo4j = neo4j_store
        self._pinecone = pinecone_store

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, workspace_id: str) -> "KnowledgeGraph":
        """
        Load a pre-built graph for *workspace_id*.

        - Loads local NetworkX pickle + node registry from /data/workspaces/{wid}/.
        - Connects to Qdrant collection `kb-{wid_short}` if USE_QDRANT.
        - Connects to Memgraph if USE_MEMGRAPH; all reads filter by workspace_id.
        """
        if not workspace_id:
            raise ValueError("KnowledgeGraph.load requires workspace_id.")

        paths = workspace_paths(workspace_id)
        logger.info("Loading KnowledgeGraph for ws=%s from %s", workspace_id, paths["graph_pickle"].parent)

        if not paths["graph_pickle"].exists() or not paths["node_registry"].exists():
            raise FileNotFoundError(
                f"No local cache for workspace {workspace_id}. "
                f"Run POST /api/v1/build for this workspace first."
            )

        with open(paths["graph_pickle"], "rb") as f:
            G: nx.DiGraph = pickle.load(f)

        with open(paths["node_registry"], encoding="utf-8") as f:
            registry_raw: dict[str, dict] = json.load(f)

        nodes = {nid: node_from_dict(data) for nid, data in registry_raw.items()}

        # --- Vector search backend ---
        faiss_index = None
        faiss_node_ids: list[str] = []
        embed_model = None
        pinecone_store = None
        qdrant_collection = f"kb-{_short_wid(workspace_id)}"

        if USE_QDRANT:
            logger.info("USE_QDRANT=True — connecting to Qdrant collection %s", qdrant_collection)
            try:
                from src.infra.qdrant_store import QdrantVectorStore
                pinecone_store = QdrantVectorStore(
                    url=QDRANT_URL,
                    api_key=QDRANT_API_KEY,
                    collection_name=qdrant_collection,
                    dimension=EMBEDDING_DIM,
                )
            except Exception as exc:
                logger.warning("Qdrant connect failed (%s); falling back to FAISS.", exc)
        elif USE_PINECONE:
            logger.info("USE_PINECONE=True — connecting to Pinecone for semantic search…")
            try:
                from src.infra.pinecone_store import PineconeVectorStore
                pinecone_store = PineconeVectorStore(
                    api_key=PINECONE_API_KEY,
                    index_name=PINECONE_INDEX_NAME,
                    namespace=f"{PINECONE_NAMESPACE}-{_short_wid(workspace_id)}",
                    dimension=EMBEDDING_DIM,
                )
            except Exception as exc:
                logger.warning("Pinecone connect failed (%s); falling back to FAISS.", exc)

        if pinecone_store is None:
            faiss_path = paths["faiss_index"]
            if faiss_path.exists():
                faiss_index, faiss_node_ids = EmbeddingPipeline.load_faiss_index(faiss_path)
                embed_model = _load_embed_model(faiss_path, nodes)

        # --- Graph backend ---
        neo4j_store = None
        if USE_MEMGRAPH:
            logger.info("USE_MEMGRAPH=True — connecting to Memgraph (ws=%s)", workspace_id)
            try:
                from src.infra.memgraph_store import MemgraphGraphStore
                neo4j_store = MemgraphGraphStore(
                    MEMGRAPH_URI, MEMGRAPH_USERNAME, MEMGRAPH_PASSWORD, MEMGRAPH_DATABASE
                )
            except Exception as exc:
                logger.warning("Memgraph connect failed (%s); using NetworkX fallback.", exc)
        elif USE_NEO4J:
            logger.info("USE_NEO4J=True — connecting to Neo4j (ws=%s)", workspace_id)
            try:
                from src.infra.neo4j_store import Neo4jGraphStore
                neo4j_store = Neo4jGraphStore(
                    NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE
                )
            except Exception as exc:
                logger.warning("Neo4j connect failed (%s); using NetworkX fallback.", exc)

        logger.info("Loaded graph ws=%s: %d nodes, %d edges",
                    workspace_id, G.number_of_nodes(), G.number_of_edges())
        kg = cls(
            G=G,
            nodes=nodes,
            workspace_id=workspace_id,
            faiss_index=faiss_index,
            faiss_node_ids=faiss_node_ids,
            neo4j_store=neo4j_store,
            pinecone_store=pinecone_store,
        )
        kg._embed_model = embed_model
        return kg

    # ------------------------------------------------------------------
    # Node lookup
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> BaseNode | None:
        return self.nodes.get(node_id)

    def get_node_data(self, node_id: str) -> dict[str, Any] | None:
        if node_id not in self.G:
            return None
        return dict(self.G.nodes[node_id])

    def get_children(self, node_id: str, edge_types: list[str] | None = None) -> list[str]:
        """Return direct children of a node filtered by edge type(s)."""
        result = []
        for _, tgt, data in self.G.out_edges(node_id, data=True):
            if edge_types is None or data.get("edge_type") in edge_types:
                result.append(tgt)
        return result

    def get_parents(self, node_id: str) -> list[str]:
        return list(self.G.predecessors(node_id))

    def get_edges(self, node_id: str) -> list[dict[str, Any]]:
        """Return all outgoing and incoming edges from a node as dicts."""
        edges = []
        for _, tgt, data in self.G.out_edges(node_id, data=True):
            edges.append({"target": tgt, **data})
        for src, _, data in self.G.in_edges(node_id, data=True):
            edges.append({"source": src, **data})
        return edges

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def bfs_traverse(
        self,
        start_ids: list[str],
        max_depth: int | None = None,
        edge_types: list[str] | None = None,
        max_nodes: int | None = None,
    ) -> list[str]:
        """BFS from multiple start nodes; returns ordered list of node IDs."""
        trv = get_graph_config().traversal
        if max_depth is None:
            max_depth = trv.max_depth
        if max_nodes is None:
            max_nodes = trv.max_nodes
        visited: list[str] = []
        seen: set[str] = set()
        queue: list[tuple[str, int]] = [(nid, 0) for nid in start_ids if nid in self.G]

        while queue and len(visited) < max_nodes:
            nid, depth = queue.pop(0)
            if nid in seen:
                continue
            seen.add(nid)
            visited.append(nid)
            if depth >= max_depth:
                continue
            for child in self.get_children(nid, edge_types):
                if child not in seen:
                    queue.append((child, depth + 1))

        return visited

    def get_subtree(self, root_id: str, max_depth: int | None = None) -> dict[str, Any]:
        """Return a nested dict tree structure rooted at root_id."""
        if max_depth is None:
            max_depth = get_graph_config().traversal.subtree_max_depth
        node = self.nodes.get(root_id)
        if not node:
            return {}
        result = node.to_dict()
        result["children"] = []
        if max_depth > 0:
            for child_id in self.get_children(root_id, [EdgeType.CONTAINS.value]):
                child_tree = self.get_subtree(child_id, max_depth - 1)
                if child_tree:
                    result["children"].append(child_tree)
        return result

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def keyword_search(
        self, query: str, top_k: int | None = None, node_types: list[NodeType] | None = None
    ) -> list[tuple[str, float]]:
        """Simple keyword match against node headings and summaries."""
        sc = get_graph_config().search
        if top_k is None:
            top_k = sc.default_top_k
        min_tok = sc.keyword_min_token_length
        query_lower = query.lower()
        tokens = [t for t in query_lower.split() if len(t) >= min_tok]
        results: list[tuple[str, float]] = []

        for nid, node in self.nodes.items():
            if node_types and node.node_type not in node_types:
                continue
            text = (node.heading + " " + node.content_summary).lower()
            score = sum(1.0 for t in tokens if t in text) / max(len(tokens), 1)
            if score > 0:
                results.append((nid, score))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def semantic_search(self, query: str, top_k: int | None = None) -> list[tuple[str, float]]:
        """
        Semantic search using Pinecone (cloud) or FAISS (local).

        Falls back to keyword search if neither backend is available.
        """
        if top_k is None:
            top_k = get_graph_config().search.default_top_k
        # --- Pinecone path ---
        if self._pinecone is not None:
            embed_model = self._embed_model
            if embed_model is None:
                # Lazily load the embed model for query-time use
                faiss_path = Path(FAISS_INDEX_PATH)
                if faiss_path.exists():
                    embed_model = _load_embed_model(faiss_path, self.nodes)
                    self._embed_model = embed_model
            if embed_model is not None:
                import numpy as np
                vec = embed_model.encode(
                    [query], normalize_embeddings=True, show_progress_bar=False
                ).astype(np.float32)
                return self._pinecone.query(vec[0].tolist(), top_k=top_k)
            else:
                logger.warning("No embed model available for Pinecone query; falling back to keyword.")
                return self.keyword_search(query, top_k)

        # --- FAISS path ---
        if self._faiss_index is None or self._embed_model is None:
            logger.warning("FAISS index or embed model not loaded; falling back to keyword search.")
            return self.keyword_search(query, top_k)

        import numpy as np
        vec = self._embed_model.encode(
            [query], normalize_embeddings=True, show_progress_bar=False
        ).astype(np.float32)
        distances, indices = self._faiss_index.search(vec, top_k)

        results: list[tuple[str, float]] = []
        for dist, pos in zip(distances[0], indices[0]):
            if pos >= 0:
                results.append((self._faiss_node_ids[pos], float(dist)))
        return results

    def hybrid_search(
        self, query: str, top_k: int | None = None
    ) -> list[tuple[str, float]]:
        """Combine semantic + keyword search and re-rank."""
        cfg = get_graph_config()
        if top_k is None:
            top_k = cfg.traversal.top_k_entry_nodes
        sem_w = cfg.search.hybrid_semantic_weight
        kw_w = cfg.search.hybrid_keyword_weight
        semantic_results = dict(self.semantic_search(query, top_k * 2))
        keyword_results = dict(self.keyword_search(query, top_k * 2))

        all_ids = set(semantic_results) | set(keyword_results)
        combined: list[tuple[str, float]] = []
        for nid in all_ids:
            sem_score = semantic_results.get(nid, 0.0)
            kw_score = keyword_results.get(nid, 0.0)
            combined.append((nid, sem_w * sem_score + kw_w * kw_score))

        combined.sort(key=lambda x: x[1], reverse=True)
        return combined[:top_k]

    # ------------------------------------------------------------------
    # Tool resolution
    # ------------------------------------------------------------------

    def get_tools_for_node(self, node_id: str) -> list[ToolNode]:
        """Find ToolNodes reachable from node_id via USES_TOOL or CONTAINS edges."""
        tool_ids: set[str] = set()

        for _, tgt, data in self.G.out_edges(node_id, data=True):
            if data.get("edge_type") == EdgeType.USES_TOOL.value:
                tool_ids.add(tgt)

        for src, _, data in self.G.in_edges(node_id, data=True):
            if data.get("edge_type") == EdgeType.IMPLEMENTS.value:
                for child_id in self.bfs_traverse([src], max_depth=3,
                                                  edge_types=[EdgeType.CONTAINS.value]):
                    child = self.nodes.get(child_id)
                    if isinstance(child, ToolNode):
                        tool_ids.add(child_id)

        return [
            self.nodes[tid]  # type: ignore[return-value]
            for tid in tool_ids
            if isinstance(self.nodes.get(tid), ToolNode)
        ]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for node in self.nodes.values():
            counts[node.node_type.value] = counts.get(node.node_type.value, 0) + 1

        edge_counts: dict[str, int] = {}
        for _, _, data in self.G.edges(data=True):
            et = data.get("edge_type", "unknown")
            edge_counts[et] = edge_counts.get(et, 0) + 1

        result: dict[str, Any] = {
            "total_nodes": self.G.number_of_nodes(),
            "total_edges": self.G.number_of_edges(),
            "nodes_by_type": counts,
            "edges_by_type": edge_counts,
            "backends": {
                "graph": (
                    "memgraph" if (self._neo4j and USE_MEMGRAPH)
                    else "neo4j" if self._neo4j
                    else "networkx"
                ),
                "vectors": (
                    "qdrant" if (self._pinecone and USE_QDRANT)
                    else "pinecone" if self._pinecone
                    else "faiss"
                ),
            },
        }

        # Augment with live Neo4j stats when available
        if self._neo4j:
            try:
                result["neo4j"] = self._neo4j.stats()
            except Exception as exc:
                result["neo4j_error"] = str(exc)

        # Augment with Pinecone stats when available
        if self._pinecone:
            try:
                result["pinecone"] = self._pinecone.stats()
            except Exception as exc:
                result["pinecone_error"] = str(exc)

        return result

    # ------------------------------------------------------------------
    # Full tree structure for API / UI
    # ------------------------------------------------------------------

    def full_tree(self) -> list[dict[str, Any]]:
        """Return the two root nodes with subtrees for UI navigation."""
        depth = get_graph_config().traversal.full_tree_depth
        roots = [
            nid for nid, node in self.nodes.items()
            if node.node_type.value == "domain"
        ]
        return [self.get_subtree(rid, max_depth=depth) for rid in roots]
