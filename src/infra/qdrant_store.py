"""
Qdrant vector store adapter.

Provides QdrantVectorStore — mirrors the same semantic interface as
PineconeVectorStore (upsert, query, batch_query, fetch, stats, delete_namespace)
so it can be dropped into the existing EmbeddingPipeline with a single
branch.

All vectors live in a single Qdrant collection (the "namespace" concept
from Pinecone maps to a Qdrant collection, since Qdrant has no nested
namespaces).  The collection is created on first connect if absent.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_UPSERT_BATCH = 100
_QUERY_BATCH = 25


def _safe_point_id(node_id: str) -> str:
    """
    Qdrant point IDs must be either UUIDs or unsigned ints. Our node_ids
    are arbitrary strings like "knowledge:ch7:s7.2-cibil-bands:tbl0", so we
    hash them to a deterministic UUID-compatible string.
    """
    h = hashlib.md5(node_id.encode("utf-8")).hexdigest()
    # Format as UUID string: 8-4-4-4-12
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


class QdrantVectorStore:
    """
    Qdrant-backed vector store.

    Parameters
    ----------
    url              : Qdrant cluster URL (e.g. https://<uuid>.<region>.aws.cloud.qdrant.io:6333)
    api_key          : Qdrant API key (JWT-format for Qdrant Cloud)
    collection_name  : Collection to upsert into (created if absent)
    dimension        : Vector dimension; used when creating a new collection
    metric           : Distance metric ("cosine" | "dot" | "euclid")
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        collection_name: str,
        dimension: int = 4096,
        metric: str = "cosine",
    ) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self._collection = collection_name
        self._dimension = dimension
        self._client = QdrantClient(url=url, api_key=api_key, timeout=60.0)
        # Map friendly metric name → Qdrant Distance enum
        distance_map = {
            "cosine":   Distance.COSINE,
            "dot":      Distance.DOT,
            "dotproduct": Distance.DOT,
            "euclid":   Distance.EUCLID,
            "euclidean": Distance.EUCLID,
        }
        self._distance = distance_map.get(metric.lower(), Distance.COSINE)

        self._ensure_collection()

        logger.info(
            f"QdrantVectorStore connected: collection='{collection_name}', "
            f"dim={dimension}, metric={metric}"
        )

    # ------------------------------------------------------------------
    # Schema management
    # ------------------------------------------------------------------

    def _ensure_collection(self) -> None:
        from qdrant_client.models import VectorParams

        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection in existing:
            info = self._client.get_collection(self._collection)
            current_dim = info.config.params.vectors.size
            if current_dim != self._dimension:
                logger.warning(
                    f"Qdrant collection '{self._collection}' has dim={current_dim}, "
                    f"but EMBEDDING_DIM={self._dimension}. Dropping and recreating."
                )
                self._client.delete_collection(self._collection)
                self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config=VectorParams(size=self._dimension, distance=self._distance),
                )
            return

        logger.info(
            f"Qdrant collection '{self._collection}' not found — creating "
            f"(dim={self._dimension}, distance={self._distance})…"
        )
        self._client.create_collection(
            collection_name=self._collection,
            vectors_config=VectorParams(size=self._dimension, distance=self._distance),
        )
        # Qdrant collection is ready immediately after create
        logger.info(f"Qdrant collection '{self._collection}' ready.")

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def upsert(
        self,
        node_ids: list[str],
        vectors: list[list[float]],
        metadata: list[dict[str, Any]],
    ) -> None:
        from qdrant_client.models import PointStruct

        total = len(node_ids)
        for start in range(0, total, _UPSERT_BATCH):
            end = min(start + _UPSERT_BATCH, total)
            points = [
                PointStruct(
                    id=_safe_point_id(node_ids[i]),
                    vector=list(vectors[i]),
                    payload={**metadata[i], "node_id": node_ids[i]},
                )
                for i in range(start, end)
            ]
            self._client.upsert(collection_name=self._collection, points=points, wait=True)
        logger.info(f"Upserted {total} vectors into Qdrant collection '{self._collection}'.")

    def delete_namespace(self) -> None:
        """Delete all vectors in the collection (clean slate before rebuild)."""
        try:
            from qdrant_client.models import Filter
            self._client.delete_collection(self._collection)
            self._ensure_collection()
            logger.info(f"Recreated Qdrant collection '{self._collection}' (clean slate).")
        except Exception as exc:
            logger.warning(f"delete_namespace failed: {exc}")

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def query(
        self,
        vector: list[float],
        top_k: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[tuple[str, float]]:
        """Query the collection for the top-k nearest neighbours.

        Uses ``query_points`` (qdrant-client >= 1.10). Falls back to
        ``search`` for older clients.
        """
        query_fn = getattr(self._client, "query_points", None)
        if query_fn is not None:
            resp = query_fn(
                collection_name=self._collection,
                query=list(vector),
                limit=top_k,
                with_payload=True,
            )
            points = resp.points
        else:
            points = self._client.search(
                collection_name=self._collection,
                query_vector=list(vector),
                limit=top_k,
                with_payload=True,
            )
        return [
            (p.payload.get("node_id", str(p.id)) if p.payload else str(p.id), float(p.score))
            for p in points
        ]

    def batch_query(
        self,
        vectors: list[list[float]],
        node_ids: list[str],
        top_k: int = 10,
    ) -> dict[str, list[tuple[str, float]]]:
        """Query for each vector; returns node_id → list of (neighbour_id, score)."""
        results: dict[str, list[tuple[str, float]]] = {}
        for i, (vec, nid) in enumerate(zip(vectors, node_ids)):
            neighbours = self.query(vec, top_k=top_k + 1)
            neighbours = [(n_id, score) for n_id, score in neighbours if n_id != nid]
            results[nid] = neighbours[:top_k]
            if (i + 1) % 50 == 0:
                logger.debug(f"batch_query progress: {i + 1}/{len(node_ids)}")
        return results

    def fetch(self, node_ids: list[str]) -> dict[str, list[float]]:
        """Fetch raw vectors by node_id."""
        point_ids = [_safe_point_id(nid) for nid in node_ids]
        resp = self._client.retrieve(
            collection_name=self._collection,
            ids=point_ids,
            with_vectors=True,
            with_payload=True,
        )
        out: dict[str, list[float]] = {}
        for point in resp:
            nid = point.payload.get("node_id") if point.payload else None
            if nid and point.vector:
                out[nid] = list(point.vector)
        return out

    def stats(self) -> dict[str, Any]:
        """Return collection stats."""
        info = self._client.get_collection(self._collection)
        return {
            "total_vector_count": info.points_count,
            "namespace_vector_count": info.points_count,
            "dimension": info.config.params.vectors.size,
            "namespace": self._collection,
        }
