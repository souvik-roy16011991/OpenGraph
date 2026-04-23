"""
Qdrant vector store adapter.

Provides QdrantVectorStore — mirrors the same semantic interface as
PineconeVectorStore (upsert, query, batch_query, fetch, stats, delete_namespace)
so it can be dropped into the existing EmbeddingPipeline with a single
branch.

Two collection modes:

1. **Per-workspace collection (legacy)** — ``workspace_id=None`` at
   construction. The whole collection is the tenant boundary; this matched
   the old "one collection per kb_id" mental model. Works, but Qdrant Cloud
   caps the per-account collection count (roughly the low thousands), so
   at 1M workspaces the account hits a hard ceiling.
2. **Shared collection + workspace_id filter (1M-scale mode)** — constructor
   receives a ``workspace_id``. Every point carries ``workspace_id`` in its
   payload; queries always inject a ``Filter(workspace_id=<wid>)`` clause;
   ``delete_namespace`` deletes *only* this workspace's points via filter,
   leaving the shared collection intact.

Mode is picked by the caller — ``src/graph_builder/embeddings.py`` passes
``workspace_id`` when env ``QDRANT_SHARED_COLLECTION`` is set. Both modes
coexist so operators can migrate workspace-by-workspace.
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
        workspace_id: str | None = None,
    ) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self._collection = collection_name
        self._dimension = dimension
        # When set, enables shared-collection mode: upsert writes
        # ``workspace_id`` into every point's payload and every read/
        # delete operation filters on it. When None, the whole collection
        # is the tenant.
        self._workspace_id = workspace_id
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
        self._ensure_workspace_payload_index()

        logger.info(
            f"Vector store connected: collection='{collection_name}', "
            f"dim={dimension}, metric={metric}, ws={workspace_id or '<per-collection>'}"
        )

    # Qdrant filters are fast only when the payload key is indexed. Create
    # the index idempotently at connect time so shared-collection queries
    # don't scan the whole collection at 100M+ points.
    def _ensure_workspace_payload_index(self) -> None:
        if self._workspace_id is None:
            return
        try:
            from qdrant_client.models import PayloadSchemaType
            self._client.create_payload_index(
                collection_name=self._collection,
                field_name="workspace_id",
                field_schema=PayloadSchemaType.KEYWORD,
            )
        except Exception as exc:
            # Already-indexed raises — that's the healthy path after the
            # first process to ever touch this collection.
            msg = str(exc).lower()
            if "already" not in msg and "exists" not in msg:
                logger.warning(
                    "Qdrant payload-index create for workspace_id failed: %s", exc,
                )

    def _ws_filter(self):
        """Return a Filter that pins reads/deletes to this workspace, or
        None when running in legacy per-collection mode."""
        if self._workspace_id is None:
            return None
        from qdrant_client.models import FieldCondition, Filter, MatchValue
        return Filter(
            must=[FieldCondition(key="workspace_id", match=MatchValue(value=self._workspace_id))]
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
        logger.info(f"Vector store collection '{self._collection}' ready.")

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
        # Shared-collection points disambiguate by both workspace_id and
        # node_id: two different tenants with the same node_id must NOT
        # collide on Qdrant's point id (hash of node_id). We prefix
        # ``{workspace_id}:{node_id}`` for the hash so each tenant's
        # vectors live at distinct point ids.
        def _pid(nid: str) -> str:
            if self._workspace_id is None:
                return _safe_point_id(nid)
            return _safe_point_id(f"{self._workspace_id}:{nid}")

        extra_payload = {"workspace_id": self._workspace_id} if self._workspace_id else {}

        for start in range(0, total, _UPSERT_BATCH):
            end = min(start + _UPSERT_BATCH, total)
            points = [
                PointStruct(
                    id=_pid(node_ids[i]),
                    vector=list(vectors[i]),
                    payload={**metadata[i], "node_id": node_ids[i], **extra_payload},
                )
                for i in range(start, end)
            ]
            self._client.upsert(collection_name=self._collection, points=points, wait=True)
        logger.info(
            f"Upserted {total} vectors into vector store collection '{self._collection}'"
            + (f" (ws={self._workspace_id})" if self._workspace_id else "")
        )

    def delete_namespace(self) -> None:
        """Clean-slate reset for this tenant before a rebuild.

        Per-collection mode: drops and recreates the whole collection.
        Shared-collection mode: issues a filter-delete that removes only
        this workspace's points. Either way, callers can upsert a fresh
        set of vectors afterward without worrying about stale IDs.
        """
        try:
            if self._workspace_id is None:
                self._client.delete_collection(self._collection)
                self._ensure_collection()
                logger.info(
                    f"Recreated vector store collection '{self._collection}' (clean slate)."
                )
            else:
                from qdrant_client.models import FilterSelector
                flt = self._ws_filter()
                if flt is None:
                    return
                self._client.delete(
                    collection_name=self._collection,
                    points_selector=FilterSelector(filter=flt),
                    wait=True,
                )
                logger.info(
                    f"Deleted workspace {self._workspace_id} vectors from shared "
                    f"vector store collection '{self._collection}' (clean slate)."
                )
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

        In shared-collection mode, a ``workspace_id`` filter is always
        applied so a tenant never sees another tenant's vectors — even if
        a caller forgets to pass ``filter=``.

        Uses ``query_points`` (qdrant-client >= 1.10). Falls back to
        ``search`` for older clients.
        """
        ws_flt = self._ws_filter()
        query_fn = getattr(self._client, "query_points", None)
        if query_fn is not None:
            kwargs = {
                "collection_name": self._collection,
                "query": list(vector),
                "limit": top_k,
                "with_payload": True,
            }
            if ws_flt is not None:
                kwargs["query_filter"] = ws_flt
            resp = query_fn(**kwargs)
            points = resp.points
        else:
            kwargs = {
                "collection_name": self._collection,
                "query_vector": list(vector),
                "limit": top_k,
                "with_payload": True,
            }
            if ws_flt is not None:
                kwargs["query_filter"] = ws_flt
            points = self._client.search(**kwargs)
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
        """Fetch raw vectors by node_id. Shared-collection mode prefixes
        the workspace_id into the hash to match the upsert path."""
        if self._workspace_id is None:
            point_ids = [_safe_point_id(nid) for nid in node_ids]
        else:
            point_ids = [_safe_point_id(f"{self._workspace_id}:{nid}") for nid in node_ids]
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
