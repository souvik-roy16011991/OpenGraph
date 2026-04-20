"""
Embedding pipeline:

Embedding strategy:
  Primary: sentence-transformers all-MiniLM-L6-v2 (requires HuggingFace network access)
  Fallback: TF-IDF vectoriser (pure Python, works offline)

  1. Generate a text embedding for every node using sentence-transformers.
  2. Build a FAISS index for semantic nearest-neighbour lookup.
  3. Add RELATED_TO edges between nodes whose cosine similarity exceeds the threshold.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from src.config import (
    EMBEDDING_DIM,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    MAX_RELATED_EDGES_PER_NODE,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    PINECONE_API_KEY,
    PINECONE_INDEX_NAME,
    PINECONE_NAMESPACE,
    QDRANT_API_KEY,
    QDRANT_COLLECTION_NAME,
    QDRANT_URL,
    SIMILARITY_THRESHOLD,
    USE_PINECONE,
    USE_QDRANT,
)
from src.graph_config import get_graph_config
from src.models.nodes import BaseNode, Edge, EdgeType, NodeType

logger = logging.getLogger(__name__)


def _skip_related_types() -> set[NodeType]:
    """Node types excluded from RELATED_TO edge generation (see graph.yaml)."""
    names = get_graph_config().embeddings.skip_related_to_types
    out: set[NodeType] = set()
    for name in names:
        try:
            out.add(NodeType(name))
        except ValueError:
            logger.warning("graph.yaml: unknown node type %r in skip_related_to_types", name)
    return out


# ---------------------------------------------------------------------------
# TF-IDF fallback embedder (pure Python / scikit-learn, works offline)
# ---------------------------------------------------------------------------

class _TFIDFEmbedder:
    """
    Lightweight hash-based text embedder.
    Uses sklearn HashingVectorizer (no fitting required) to produce
    EMBEDDING_DIM-dimensional normalised vectors instantly.
    No SVD, no network access, works completely offline.
    """

    def __init__(self) -> None:
        from sklearn.feature_extraction.text import HashingVectorizer

        self._vectorizer = HashingVectorizer(
            n_features=get_graph_config().embeddings.tfidf_fallback_dim,
            ngram_range=(1, 2),
            norm="l2",
            alternate_sign=False,
            dtype=np.float32,
        )
        self._fitted = True   # HashingVectorizer requires no fitting

    def fit(self, texts: list[str]) -> None:
        # No-op: HashingVectorizer needs no fitting
        self._fitted = True

    def encode(
        self,
        sentences: list[str],
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        # transform returns scipy sparse; convert to dense
        vecs = self._vectorizer.transform(sentences).toarray().astype(np.float32)
        if normalize_embeddings:
            norms = np.linalg.norm(vecs, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)
            vecs = vecs / norms
        return vecs


# ---------------------------------------------------------------------------
# OpenRouter API embedder (remote, batched, OpenAI-compatible)
# ---------------------------------------------------------------------------

class _OpenRouterEmbedder:
    """
    Calls POST /api/v1/embeddings on OpenRouter, returns L2-normalised ndarray.

    Matches the `.encode(sentences, normalize_embeddings, show_progress_bar)`
    interface of sentence-transformers so all downstream callers are unchanged.
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        dimensions: int | None = None,
    ) -> None:
        from openai import OpenAI
        self._model = model
        self._dimensions = dimensions
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def encode(
        self,
        sentences: list[str],
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        all_vecs: list[list[float]] = []
        batch_size = get_graph_config().embeddings.openrouter_batch_size
        for i in range(0, len(sentences), batch_size):
            batch = sentences[i : i + batch_size]
            kwargs: dict = {
                "model": self._model,
                "input": batch,
                "encoding_format": "float",
            }
            if self._dimensions:
                kwargs["dimensions"] = self._dimensions
            resp = self._client.embeddings.create(**kwargs)
            # Sort by index to preserve input order
            batch_vecs = [
                item.embedding
                for item in sorted(resp.data, key=lambda x: x.index)
            ]
            all_vecs.extend(batch_vecs)

        arr = np.array(all_vecs, dtype=np.float32)
        if normalize_embeddings:
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            arr = arr / np.where(norms == 0, 1.0, norms)
        return arr


def _node_text(node: BaseNode) -> str:
    """Build the embedding input text for a node."""
    parts = [node.heading]
    if node.content_summary:
        parts.append(node.content_summary)
    return " ".join(parts)[: get_graph_config().embeddings.input_max_chars]


class EmbeddingPipeline:
    """
    Generates embeddings, builds a FAISS index, and returns RELATED_TO edges.
    """

    def __init__(self, nodes: dict[str, BaseNode]) -> None:
        self.nodes = nodes
        self._model: Any = None
        self._index: Any = None
        self._node_ids: list[str] = []

    # ------------------------------------------------------------------
    def _load_model(self) -> Any:
        if self._model is None:
            import os
            force_tfidf = os.environ.get("KB_FORCE_TFIDF", "").lower() in ("1", "true", "yes")
            if force_tfidf:
                logger.info("KB_FORCE_TFIDF set – using TF-IDF embedder.")
                self._model = _TFIDFEmbedder()
            elif "/" in EMBEDDING_MODEL:
                logger.info(f"Loading OpenRouter embedding model: {EMBEDDING_MODEL}")
                self._model = _OpenRouterEmbedder(
                    EMBEDDING_MODEL, OPENROUTER_API_KEY, OPENROUTER_BASE_URL, EMBEDDING_DIMENSIONS
                )
            else:
                logger.info(f"Loading sentence-transformer model: {EMBEDDING_MODEL}")
                try:
                    from sentence_transformers import SentenceTransformer
                    self._model = SentenceTransformer(EMBEDDING_MODEL, local_files_only=True)
                except Exception as exc:
                    logger.warning(
                        f"sentence-transformers load failed ({exc}). "
                        "Falling back to TF-IDF embeddings."
                    )
                    self._model = _TFIDFEmbedder()
        return self._model

    # ------------------------------------------------------------------
    def generate_embeddings(self) -> dict[str, list[float]]:
        """
        Generate embeddings for all nodes and attach them to node objects.
        Returns a dict node_id -> embedding vector.
        """
        model = self._load_model()

        skip_types = _skip_related_types()
        eligible = [
            (nid, node)
            for nid, node in self.nodes.items()
            if node.node_type not in skip_types
        ]

        self._node_ids = [nid for nid, _ in eligible]
        texts = [_node_text(node) for _, node in eligible]

        logger.info(f"Generating embeddings for {len(texts)} nodes…")

        from tqdm import tqdm
        batch_size = get_graph_config().embeddings.local_batch_size
        all_embeddings: list[list[float]] = []
        for i in tqdm(range(0, len(texts), batch_size), desc="Embedding"):
            batch = texts[i: i + batch_size]
            vecs = model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
            all_embeddings.extend(vecs.tolist())

        # Attach embeddings back to nodes
        embedding_dict: dict[str, list[float]] = {}
        for nid, vec in zip(self._node_ids, all_embeddings):
            self.nodes[nid].embedding = vec
            embedding_dict[nid] = vec

        logger.info("Embeddings generated.")
        return embedding_dict

    # ------------------------------------------------------------------
    def build_faiss_index(self, embeddings: dict[str, list[float]]) -> Any:
        """Build and return a FAISS flat inner-product index."""
        import faiss

        vecs = np.array([embeddings[nid] for nid in self._node_ids], dtype=np.float32)
        dim = vecs.shape[1]

        logger.info(f"Building FAISS index (dim={dim}, n={len(vecs)})…")
        index = faiss.IndexFlatIP(dim)  # cosine similarity (vectors are already normalised)
        index.add(vecs)
        self._index = index
        return index

    # ------------------------------------------------------------------
    def upsert_to_pinecone(self, embeddings: dict[str, list[float]]) -> Any:
        """
        Upsert all node embeddings into Pinecone.

        Returns the PineconeVectorStore instance (stored as self._pinecone_store).
        """
        from src.infra.pinecone_store import PineconeVectorStore

        store = PineconeVectorStore(
            api_key=PINECONE_API_KEY,
            index_name=PINECONE_INDEX_NAME,
            namespace=PINECONE_NAMESPACE,
            dimension=EMBEDDING_DIM,
        )
        store.delete_namespace()  # clean slate for rebuild

        node_ids = self._node_ids
        vectors = [embeddings[nid] for nid in node_ids]
        metadata = [
            {
                "node_type": self.nodes[nid].node_type.value,
                "kb_source": self.nodes[nid].kb_source.value,
                "heading": self.nodes[nid].heading[:200],
            }
            for nid in node_ids
        ]
        store.upsert(node_ids, vectors, metadata)
        self._pinecone_store = store
        return store

    # ------------------------------------------------------------------
    def upsert_to_qdrant(
        self,
        embeddings: dict[str, list[float]],
        collection_name: str | None = None,
    ) -> Any:
        """
        Upsert all node embeddings into Qdrant Cloud.

        Returns the QdrantVectorStore instance.
        """
        from src.infra.qdrant_store import QdrantVectorStore

        node_ids = self._node_ids
        # Dimension must match what we actually generated (not the configured default)
        actual_dim = len(next(iter(embeddings.values()))) if embeddings else EMBEDDING_DIM

        store = QdrantVectorStore(
            url=QDRANT_URL,
            api_key=QDRANT_API_KEY,
            collection_name=collection_name or QDRANT_COLLECTION_NAME,
            dimension=actual_dim,
        )
        store.delete_namespace()  # clean slate for rebuild

        vectors = [embeddings[nid] for nid in node_ids]
        metadata = [
            {
                "node_type": self.nodes[nid].node_type.value,
                "kb_source": self.nodes[nid].kb_source.value,
                "heading": self.nodes[nid].heading[:200],
            }
            for nid in node_ids
        ]
        store.upsert(node_ids, vectors, metadata)
        return store

    # ------------------------------------------------------------------
    def build_related_edges(
        self,
        embeddings: dict[str, list[float]],
        pinecone_store: Any = None,
        remote_store: Any = None,
    ) -> list[Edge]:
        """
        For every node, find its nearest neighbours and add RELATED_TO edges
        where similarity > SIMILARITY_THRESHOLD.

        Uses remote_store.batch_query (Pinecone or Qdrant) when a store is passed;
        falls back to FAISS. `pinecone_store` is kept for backwards compatibility.
        """
        store = remote_store or pinecone_store
        k = MAX_RELATED_EDGES_PER_NODE + 1  # +1 to exclude self-match
        logger.info("Building RELATED_TO edges…")

        edges: list[Edge] = []
        seen: set[frozenset] = set()

        if store is not None:
            # --- Remote vector store path (Pinecone or Qdrant) ---
            vectors = [embeddings[nid] for nid in self._node_ids]
            nn_map = store.batch_query(vectors, self._node_ids, top_k=k)

            for src_id, neighbours in nn_map.items():
                src_node = self.nodes[src_id]
                for tgt_id, score in neighbours:
                    if float(score) < SIMILARITY_THRESHOLD:
                        continue
                    tgt_node = self.nodes.get(tgt_id)
                    if tgt_node is None:
                        continue
                    if src_node.parent_id and src_node.parent_id == tgt_node.parent_id:
                        continue
                    pair = frozenset([src_id, tgt_id])
                    if pair in seen:
                        continue
                    seen.add(pair)
                    edges.append(Edge(
                        source_id=src_id,
                        target_id=tgt_id,
                        edge_type=EdgeType.RELATED_TO,
                        weight=float(score),
                        metadata={"similarity": float(score)},
                    ))
        else:
            # --- FAISS path ---
            import faiss  # noqa: F401

            if self._index is None:
                self.build_faiss_index(embeddings)

            index = self._index
            vecs = np.array([embeddings[nid] for nid in self._node_ids], dtype=np.float32)
            distances, indices = index.search(vecs, k)

            for src_pos, (dists, nbr_positions) in enumerate(zip(distances, indices)):
                src_id = self._node_ids[src_pos]
                src_node = self.nodes[src_id]

                for dist, nbr_pos in zip(dists, nbr_positions):
                    if nbr_pos < 0 or nbr_pos == src_pos:
                        continue
                    if float(dist) < SIMILARITY_THRESHOLD:
                        continue
                    tgt_id = self._node_ids[nbr_pos]
                    tgt_node = self.nodes[tgt_id]

                    if src_node.parent_id and src_node.parent_id == tgt_node.parent_id:
                        continue

                    pair = frozenset([src_id, tgt_id])
                    if pair in seen:
                        continue
                    seen.add(pair)

                    edges.append(Edge(
                        source_id=src_id,
                        target_id=tgt_id,
                        edge_type=EdgeType.RELATED_TO,
                        weight=float(dist),
                        metadata={"similarity": float(dist)},
                    ))

        logger.info(f"RELATED_TO edges created: {len(edges)}")
        return edges


def run_embedding_pipeline(
    nodes: dict[str, BaseNode],
    workspace_id: str | None = None,
    qdrant_collection: str | None = None,
) -> tuple[dict[str, list[float]], list[Edge], Any]:
    """
    Top-level entry: generate embeddings, store vectors, build RELATED_TO edges.

    Returns (embedding_dict, related_edges, vector_store). Vectors live in
    Qdrant Cloud (preferred) or Pinecone — the FAISS code path is kept only
    for offline CLI usage and is never persisted to disk when a cloud vector
    store is configured.
    """
    pipeline = EmbeddingPipeline(nodes)
    embeddings = pipeline.generate_embeddings()

    if USE_QDRANT:
        logger.info("USE_QDRANT=True – upserting vectors to Qdrant Cloud (collection=%s)…",
                    qdrant_collection or "default")
        vector_store = pipeline.upsert_to_qdrant(embeddings, collection_name=qdrant_collection)
        related_edges = pipeline.build_related_edges(embeddings, remote_store=vector_store)
        return embeddings, related_edges, vector_store

    if USE_PINECONE:
        logger.info("USE_PINECONE=True – upserting vectors to Pinecone…")
        vector_store = pipeline.upsert_to_pinecone(embeddings)
        related_edges = pipeline.build_related_edges(embeddings, pinecone_store=vector_store)
        return embeddings, related_edges, vector_store

    # Offline / CLI path: build an in-memory FAISS index only. Not persisted.
    logger.info("USE_QDRANT=False, USE_PINECONE=False – building in-memory FAISS index (not persisted)…")
    index = pipeline.build_faiss_index(embeddings)
    related_edges = pipeline.build_related_edges(embeddings)
    return embeddings, related_edges, index
