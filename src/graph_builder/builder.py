"""
Graph builder orchestrator — cloud-only persistence.

Pipeline:
  1. Parse KB JSON sources (bytes from Vercel Blob) -> ParsedKB objects
  2. Extract nodes -> dict[node_id, BaseNode]
  3. Build structural + cross-KB edges
  4. Generate embeddings + vector store (Qdrant or Pinecone) + RELATED_TO edges
  5. Persist graph: Memgraph (cloud) or Neo4j (cloud)

Also provides KnowledgeGraph – the runtime graph object loaded by the agent.
The in-memory NetworkX DiGraph is used as a fast runtime cache only — it is
never persisted to disk. On cold start, ``KnowledgeGraph.load()`` rehydrates
the NetworkX view directly from the Memgraph (or Neo4j) workspace; no pickle
files touch the local filesystem.
"""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx

from src.config import (
    EMBEDDING_DIM,
    MEMGRAPH_DATABASE,
    MEMGRAPH_PASSWORD,
    MEMGRAPH_URI,
    MEMGRAPH_USERNAME,
    NEO4J_DATABASE,
    NEO4J_PASSWORD,
    NEO4J_URI,
    NEO4J_USERNAME,
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
from src.graph_config import get_graph_config
from src.kb_config import get_active_kb_config
from src.graph_builder.edges import build_edges
from src.graph_builder.embeddings import run_embedding_pipeline
from src.graph_builder.extractor import extract_all_nodes
from src.graph_builder.parser import KBSourceInput, parse_kb_files
from src.workspace_context import get_current_workspace
from src.models.nodes import (
    BaseNode,
    Edge,
    EdgeType,
    NodeType,
    ToolNode,
    node_from_dict,
)

logger = logging.getLogger(__name__)


def _build_query_embedder(nodes: dict) -> Any:
    """
    Build a fresh query-time embedder for semantic_search when we have a
    remote vector store (Qdrant/Pinecone). No persisted model state is read
    from disk — the model is reconstructed in-memory from the configured
    EMBEDDING_MODEL. For TF-IDF (hash-based, deterministic) this is a no-op
    rebuild; for remote OpenRouter / sentence-transformers it's a network /
    local-cache init.
    """
    from src.graph_builder.embeddings import _TFIDFEmbedder, _OpenRouterEmbedder, _node_text
    from src.config import (
        EMBEDDING_DIMENSIONS,
        EMBEDDING_MODEL,
        OPENROUTER_API_KEY,
        OPENROUTER_BASE_URL,
    )
    import os

    force_tfidf = os.environ.get("KB_FORCE_TFIDF", "").lower() in ("1", "true", "yes")
    if force_tfidf:
        model = _TFIDFEmbedder()
        model.fit([_node_text(n) for n in nodes.values()])
        return model
    if "/" in EMBEDDING_MODEL:
        logger.info(f"Loading OpenRouter embedding model: {EMBEDDING_MODEL}")
        return _OpenRouterEmbedder(
            EMBEDDING_MODEL, OPENROUTER_API_KEY, OPENROUTER_BASE_URL, EMBEDDING_DIMENSIONS
        )
    try:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading sentence-transformer model {EMBEDDING_MODEL}")
        return SentenceTransformer(EMBEDDING_MODEL)
    except Exception as exc:
        logger.warning(f"sentence-transformers failed ({exc}); using TF-IDF fallback.")
        model = _TFIDFEmbedder()
        model.fit([_node_text(n) for n in nodes.values()])
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
    knowledge_sources: list[KBSourceInput] | None = None,
    tool_sources: list[KBSourceInput] | None = None,
) -> "KnowledgeGraph":
    """
    Full build pipeline for a workspace. Returns a KnowledgeGraph ready for queries.

    Args:
        use_llm_cross_links: Whether to call the LLM for cross-KB IMPLEMENTS edges.
        skip_embeddings: Set True to skip embedding generation (faster during dev).
        workspace_id: REQUIRED — every artifact (Memgraph nodes, Qdrant
            collection, Neon rows) is scoped to this id.
        knowledge_sources, tool_sources: lists of ``(filename, bytes)`` tuples
            fetched from Vercel Blob, or Paths for legacy CLI usage. If None,
            falls back to ``get_active_kb_config()`` single-file paths.
    """
    if workspace_id is None:
        workspace_id = get_current_workspace()
    if not workspace_id:
        raise ValueError(
            "build_graph requires workspace_id (explicit arg or via workspace_context)."
        )

    qdrant_collection = f"kb-{_short_wid(workspace_id)}"

    logger.info("=== KB Knowledge Graph Build Pipeline (ws=%s) ===", workspace_id)
    if USE_MEMGRAPH:
        logger.info("  Graph store (workspace-scoped)")
    elif USE_NEO4J:
        logger.info("  Graph store (workspace-scoped)")
    else:
        logger.info("  Graph store (in-memory only)")
    if USE_QDRANT:
        logger.info("  Vector DB — collection %s", qdrant_collection)
    elif USE_PINECONE:
        logger.info("  Vector DB")
    else:
        logger.info("  Vector DB (in-memory only)")

    # 1. Parse — multi-source aware (bytes-from-Blob or legacy Paths)
    logger.info("Step 1/5 – Parsing KB files…")
    if knowledge_sources is None or tool_sources is None:
        cfg = get_active_kb_config()
        if knowledge_sources is None:
            knowledge_sources = [cfg.knowledge_kb_path]
        if tool_sources is None:
            tool_sources = [cfg.tool_kb_path]

    knowledge_kb = parse_kb_files(knowledge_sources, "knowledge")
    tool_kb = parse_kb_files(tool_sources, "tool")
    logger.info(
        "  Knowledge KB: %d chapters across %d source(s) | Tool KB: %d chapters across %d source(s)",
        len(knowledge_kb.chapters), len(knowledge_sources),
        len(tool_kb.chapters), len(tool_sources),
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

    # Record an approximate payload-bytes number into the per-build metrics
    # accumulator (no-op if no build is observing). Done here — after all
    # edges (including RELATED_TO) are final — so the number reflects what
    # actually gets written to Memgraph.
    try:
        from src.observability.usage import (
            measure_graph_payload_bytes,
            record_graph_payload_bytes,
        )
        record_graph_payload_bytes(measure_graph_payload_bytes(nodes, edges))
    except Exception:
        pass

    # 5. Persist graph (per-workspace) — cloud only; no local pickle/JSON.
    neo4j_store = None
    G: nx.DiGraph | None = None

    if USE_MEMGRAPH:
        logger.info("Step 5/5 – Writing to graph store (ws=%s)…", workspace_id)
        from src.infra.memgraph_store import MemgraphGraphStore
        neo4j_store = MemgraphGraphStore(
            MEMGRAPH_URI, MEMGRAPH_USERNAME, MEMGRAPH_PASSWORD, MEMGRAPH_DATABASE
        )
        neo4j_store.clear_workspace(workspace_id)
        neo4j_store.bulk_create_nodes_no_apoc(nodes, workspace_id=workspace_id)
        neo4j_store.bulk_create_edges(edges, workspace_id=workspace_id)
        ws_stats = neo4j_store.stats(workspace_id=workspace_id)
        logger.info(
            "  Graph store ws=%s: %d nodes, %d edges",
            workspace_id, ws_stats["total_nodes"], ws_stats["total_edges"],
        )
        G = _build_networkx_graph(nodes, edges)
        logger.info("  NetworkX cache (in-memory): %d nodes, %d edges",
                    G.number_of_nodes(), G.number_of_edges())
    elif USE_NEO4J:
        logger.info("Step 5/5 – Writing to graph store (ws=%s)…", workspace_id)
        from src.infra.neo4j_store import Neo4jGraphStore
        neo4j_store = Neo4jGraphStore(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE)
        neo4j_store.clear_workspace(workspace_id)
        neo4j_store.bulk_create_nodes_no_apoc(nodes, workspace_id=workspace_id)
        neo4j_store.bulk_create_edges(edges, workspace_id=workspace_id)
        ws_stats = neo4j_store.stats(workspace_id=workspace_id)
        logger.info("  Graph store ws=%s: %d nodes, %d edges",
                    workspace_id, ws_stats["total_nodes"], ws_stats["total_edges"])
        G = _build_networkx_graph(nodes, edges)
        logger.info("  NetworkX cache (in-memory): %d nodes, %d edges",
                    G.number_of_nodes(), G.number_of_edges())
    else:
        logger.info("Step 5/5 – Populating NetworkX DiGraph (in-memory, ws=%s)…", workspace_id)
        G = _build_networkx_graph(nodes, edges)
        logger.info("  Graph: %d nodes, %d edges", G.number_of_nodes(), G.number_of_edges())

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


def _hydrate_from_neo4j(
    neo4j_store: Any, workspace_id: str
) -> tuple[nx.DiGraph, dict[str, BaseNode]]:
    """Rebuild a NetworkX view + node registry from Memgraph/Neo4j.

    Replaces the legacy pickle+JSON cache on local disk — called at
    ``KnowledgeGraph.load()`` cold-start and any time we need a fresh
    in-memory view without touching the filesystem.
    """
    driver = neo4j_store._driver
    database = neo4j_store._database

    G = nx.DiGraph()
    nodes: dict[str, BaseNode] = {}

    with driver.session(database=database) as s:
        node_rows = s.run(
            "MATCH (n:KBNode {workspace_id: $wid}) RETURN properties(n) AS props",
            wid=workspace_id,
        )
        for rec in node_rows:
            props = dict(rec["props"])
            node_id = props.get("node_id")
            if not node_id:
                continue
            try:
                node = node_from_dict(props)
            except Exception as exc:
                logger.debug("node_from_dict failed for %s: %s", node_id, exc)
                continue
            nodes[node_id] = node
            G.add_node(node_id, **node.to_dict())

        edge_rows = s.run(
            """
            MATCH (src:KBNode {workspace_id: $wid})-[r]->(tgt:KBNode {workspace_id: $wid})
            RETURN src.node_id AS source_id, tgt.node_id AS target_id,
                   type(r) AS edge_type, properties(r) AS props
            """,
            wid=workspace_id,
        )
        for rec in edge_rows:
            src_id = rec["source_id"]
            tgt_id = rec["target_id"]
            if src_id in G and tgt_id in G:
                edge_props = dict(rec["props"] or {})
                edge_props["edge_type"] = rec["edge_type"]
                G.add_edge(src_id, tgt_id, **edge_props)

    logger.info(
        "Hydrated NetworkX view from %s: %d nodes, %d edges (ws=%s)",
        neo4j_store.__class__.__name__, G.number_of_nodes(), G.number_of_edges(), workspace_id,
    )
    return G, nodes


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
        Load a pre-built graph for *workspace_id* — cloud-only.

        - Connects to Memgraph (or Neo4j) and rehydrates a NetworkX view from
          every KBNode / relationship tagged with this workspace_id.
        - Connects to Qdrant (or Pinecone) for semantic search. No FAISS
          local index and no pickle/JSON cache on disk are touched.
        """
        if not workspace_id:
            raise ValueError("KnowledgeGraph.load requires workspace_id.")

        logger.info("Loading KnowledgeGraph for ws=%s (cloud hydrate)", workspace_id)

        # --- Graph backend (required for cloud-only loads) ---
        neo4j_store = None
        if USE_MEMGRAPH:
            logger.info("Connecting to graph store (ws=%s)", workspace_id)
            from src.infra.memgraph_store import MemgraphGraphStore
            neo4j_store = MemgraphGraphStore(
                MEMGRAPH_URI, MEMGRAPH_USERNAME, MEMGRAPH_PASSWORD, MEMGRAPH_DATABASE
            )
        elif USE_NEO4J:
            logger.info("Connecting to graph store (ws=%s)", workspace_id)
            from src.infra.neo4j_store import Neo4jGraphStore
            neo4j_store = Neo4jGraphStore(
                NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE
            )
        else:
            raise RuntimeError(
                "KnowledgeGraph.load requires USE_MEMGRAPH or USE_NEO4J — the "
                "cloud graph store is the authoritative source of the graph."
            )

        G, nodes = _hydrate_from_neo4j(neo4j_store, workspace_id)
        if G.number_of_nodes() == 0:
            raise FileNotFoundError(
                f"No graph found in cloud store for workspace {workspace_id}. "
                f"Run POST /api/v1/build for this workspace first."
            )

        # --- Vector search backend ---
        pinecone_store = None
        from src.config import QDRANT_SHARED_COLLECTION as _SHARED
        # Query-time collection + workspace_id pair mirrors the write path
        # in ``upsert_to_qdrant``: shared mode uses the single env-configured
        # collection with a workspace_id filter; legacy mode uses one
        # collection per workspace.
        qdrant_collection = _SHARED if _SHARED else f"kb-{_short_wid(workspace_id)}"
        qdrant_ws = workspace_id if _SHARED else None

        if USE_QDRANT:
            logger.info(
                "Connecting to vector DB (collection %s, ws=%s)",
                qdrant_collection, qdrant_ws or "<per-collection>",
            )
            from src.infra.qdrant_store import QdrantVectorStore
            pinecone_store = QdrantVectorStore(
                url=QDRANT_URL,
                api_key=QDRANT_API_KEY,
                collection_name=qdrant_collection,
                dimension=EMBEDDING_DIM,
                workspace_id=qdrant_ws,
            )
        elif USE_PINECONE:
            logger.info("Connecting to vector DB for semantic search…")
            from src.infra.pinecone_store import PineconeVectorStore
            pinecone_store = PineconeVectorStore(
                api_key=PINECONE_API_KEY,
                index_name=PINECONE_INDEX_NAME,
                namespace=f"{PINECONE_NAMESPACE}-{_short_wid(workspace_id)}",
                dimension=EMBEDDING_DIM,
            )

        embed_model = _build_query_embedder(nodes) if pinecone_store is not None else None

        logger.info("Loaded graph ws=%s: %d nodes, %d edges",
                    workspace_id, G.number_of_nodes(), G.number_of_edges())
        kg = cls(
            G=G,
            nodes=nodes,
            workspace_id=workspace_id,
            faiss_index=None,
            faiss_node_ids=[],
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
        Semantic search via the cloud vector store (Qdrant / Pinecone).

        Falls back to an in-memory FAISS index only when the cloud store is
        unavailable at load time. Never reads a FAISS index file from disk.
        """
        if top_k is None:
            top_k = get_graph_config().search.default_top_k

        # --- Remote vector store path (Qdrant or Pinecone) ---
        if self._pinecone is not None:
            if self._embed_model is None:
                self._embed_model = _build_query_embedder(self.nodes)
            import numpy as np
            vec = self._embed_model.encode(
                [query], normalize_embeddings=True, show_progress_bar=False
            ).astype(np.float32)
            return self._pinecone.query(vec[0].tolist(), top_k=top_k)

        # --- In-memory FAISS fallback (only set when remote vector store is down) ---
        if self._faiss_index is None or self._embed_model is None:
            logger.warning("No vector backend available; falling back to keyword search.")
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

        # Augment with live Neo4j stats when available — scoped to this workspace.
        if self._neo4j:
            try:
                result["neo4j"] = self._neo4j.stats(workspace_id=self.workspace_id)
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
