"""
Pinecone vector store adapter.

Provides PineconeVectorStore — a thin wrapper around the Pinecone Python SDK
with the same semantic interface used by the FAISS-based embedding pipeline.

All vectors live in a single namespace ("kb-knowledge-graph") inside the
"kb-index" Pinecone index, acting as the logical "folder" for this project.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_UPSERT_BATCH = 100    # Pinecone recommends ≤100 vectors per upsert call
_QUERY_BATCH  = 25     # parallel query batches for build_related_edges


class PineconeVectorStore:
    """
    Pinecone-backed vector store.

    Parameters
    ----------
    api_key      : Pinecone API key
    index_name   : Name of the Pinecone index (created if absent)
    namespace    : Logical partition inside the index (the project "folder")
    dimension    : Vector dimension; used when creating a new index
    metric       : Distance metric ("cosine" | "dotproduct" | "euclidean")
    """

    def __init__(
        self,
        api_key: str,
        index_name: str,
        namespace: str,
        dimension: int = 4096,
        metric: str = "cosine",
    ) -> None:
        from pinecone import Pinecone, ServerlessSpec

        self._namespace = namespace
        self._pc = Pinecone(api_key=api_key)

        existing = [idx.name for idx in self._pc.list_indexes()]
        if index_name not in existing:
            logger.info(
                f"Pinecone index '{index_name}' not found – creating "
                f"(dim={dimension}, metric={metric})…"
            )
            self._pc.create_index(
                name=index_name,
                dimension=dimension,
                metric=metric,
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
            )
            # Wait until ready
            for _ in range(30):
                status = self._pc.describe_index(index_name).status
                if status.get("ready"):
                    break
                time.sleep(2)
            logger.info(f"Pinecone index '{index_name}' ready.")
        else:
            existing_dim = self._pc.describe_index(index_name).dimension
            if existing_dim != dimension:
                logger.warning(
                    f"Pinecone index '{index_name}' has dimension {existing_dim}, "
                    f"but EMBEDDING_DIM={dimension}. Using existing dimension."
                )

        self._index = self._pc.Index(index_name)
        logger.info(
            f"PineconeVectorStore connected: index='{index_name}', "
            f"namespace='{namespace}'"
        )

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def upsert(
        self,
        node_ids: list[str],
        vectors: list[list[float]],
        metadata: list[dict[str, Any]],
    ) -> None:
        """
        Batch-upsert vectors with metadata into the namespace.

        Pinecone accepts max 100 vectors per call; we chunk automatically.
        """
        total = len(node_ids)
        for start in range(0, total, _UPSERT_BATCH):
            end = min(start + _UPSERT_BATCH, total)
            batch = [
                {"id": node_ids[i], "values": vectors[i], "metadata": metadata[i]}
                for i in range(start, end)
            ]
            self._index.upsert(vectors=batch, namespace=self._namespace)
        logger.info(f"Upserted {total} vectors into namespace '{self._namespace}'.")

    def delete_namespace(self) -> None:
        """Delete all vectors in the namespace (clean slate before rebuild)."""
        try:
            self._index.delete(delete_all=True, namespace=self._namespace)
            logger.info(f"Deleted all vectors in namespace '{self._namespace}'.")
        except Exception as exc:
            logger.warning(f"delete_namespace failed (maybe already empty): {exc}")

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        """
        Query the namespace for the top-k nearest neighbours.

        Returns a list of (node_id, score) pairs sorted by descending score.
        """
        kwargs: dict[str, Any] = {
            "vector": vector,
            "top_k": top_k,
            "namespace": self._namespace,
            "include_metadata": False,
        }
        if filter:
            kwargs["filter"] = filter

        resp = self._index.query(**kwargs)
        return [(m.id, m.score) for m in resp.matches]

    def batch_query(
        self,
        vectors: list[list[float]],
        node_ids: list[str],
        top_k: int = 10,
    ) -> dict[str, list[tuple[str, float]]]:
        """
        Query for each vector in the list and return a dict mapping
        node_id → list of (neighbour_id, score).

        Used during graph build to generate RELATED_TO edges.
        """
        results: dict[str, list[tuple[str, float]]] = {}
        for i, (vec, nid) in enumerate(zip(vectors, node_ids)):
            neighbours = self.query(vec, top_k=top_k + 1)  # +1 to exclude self
            # Exclude self-match
            neighbours = [(n_id, score) for n_id, score in neighbours if n_id != nid]
            results[nid] = neighbours[:top_k]
            if (i + 1) % 50 == 0:
                logger.debug(f"batch_query progress: {i + 1}/{len(node_ids)}")
        return results

    def fetch(self, node_ids: list[str]) -> dict[str, list[float]]:
        """Fetch raw vectors by ID (useful for debugging / verification)."""
        resp = self._index.fetch(ids=node_ids, namespace=self._namespace)
        return {vid: v.values for vid, v in resp.vectors.items()}

    def stats(self) -> dict[str, Any]:
        """Return index stats from Pinecone."""
        info = self._index.describe_index_stats()
        ns_stats = info.namespaces.get(self._namespace, {})
        return {
            "total_vector_count": info.total_vector_count,
            "namespace_vector_count": getattr(ns_stats, "vector_count", 0),
            "dimension": info.dimension,
            "namespace": self._namespace,
        }
