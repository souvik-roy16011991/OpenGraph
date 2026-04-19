"""
Memgraph graph store adapter.

Thin subclass of Neo4jGraphStore that overrides the few Cypher dialect
differences between Memgraph and Neo4j:

  - Constraints use the older ``CREATE CONSTRAINT ON (n:Label) ASSERT ...``
    form (no ``IF NOT EXISTS``, no constraint naming). We query
    ``SHOW CONSTRAINT INFO`` first and skip creation if the constraint
    already exists (idempotency).
  - Memgraph Community Edition is single-database; ``session(database=...)``
    is accepted but effectively ignored.

All read/write queries (MATCH, MERGE, UNWIND, variable-length paths, UNION,
DETACH DELETE, etc.) are Cypher-compatible between Memgraph 2.x and Neo4j 5.x,
so they inherit unchanged from ``Neo4jGraphStore``.
"""

from __future__ import annotations

import logging

from src.infra.neo4j_store import Neo4jGraphStore

logger = logging.getLogger(__name__)


class MemgraphGraphStore(Neo4jGraphStore):
    """Neo4jGraphStore with Memgraph-specific schema setup."""

    def _ensure_constraints(self) -> None:
        with self._driver.session(database=self._database) as s:
            existing = s.run("SHOW CONSTRAINT INFO").data()
            for row in existing:
                labels = row.get("label") or row.get("labels") or []
                props = row.get("properties") or row.get("property") or []
                if isinstance(labels, str):
                    labels = [labels]
                if isinstance(props, str):
                    props = [props]
                if "KBNode" in labels and "node_id" in props:
                    logger.debug("Memgraph: uniqueness constraint on :KBNode(node_id) already exists")
                    return

            s.run(
                "CREATE CONSTRAINT ON (n:KBNode) ASSERT n.node_id IS UNIQUE"
            )
            logger.info("Memgraph: created uniqueness constraint on :KBNode(node_id)")
