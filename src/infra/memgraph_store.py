"""
Memgraph graph store adapter.

Thin subclass of Neo4jGraphStore that overrides the few Cypher dialect
differences between Memgraph and Neo4j:

  - Multi-workspace tenancy: node_id is NOT globally unique — two workspaces
    each own a `knowledge:root` etc. We drop any legacy single-property
    (node_id) unique constraint and use plain indexes on (workspace_id) and
    (node_id) for MERGE performance. Composite unique constraints aren't
    supported by Memgraph's standard CONSTRAINT syntax, so we rely on the
    MERGE pattern ``MERGE (n:KBNode {workspace_id: X, node_id: Y})`` plus
    per-workspace clear-before-build to keep data clean.
  - Memgraph Community Edition is single-database; ``session(database=...)``
    is accepted but effectively ignored.

All read/write queries inherit unchanged from ``Neo4jGraphStore``.
"""

from __future__ import annotations

import logging

from src.infra.neo4j_store import Neo4jGraphStore

logger = logging.getLogger(__name__)


class MemgraphGraphStore(Neo4jGraphStore):
    """Neo4jGraphStore with Memgraph-specific schema setup."""

    def _ensure_constraints(self) -> None:
        with self._driver.session(database=self._database) as s:
            # 1. Drop any legacy single-property unique constraint on node_id.
            try:
                existing = s.run("SHOW CONSTRAINT INFO").data()
                for row in existing:
                    labels = row.get("label") or row.get("labels") or []
                    props = row.get("properties") or row.get("property") or []
                    ctype = row.get("constraint type") or row.get("type") or ""
                    if isinstance(labels, str):
                        labels = [labels]
                    if isinstance(props, str):
                        props = [props]
                    if "KBNode" in labels and props == ["node_id"] and "unique" in str(ctype).lower():
                        try:
                            s.run("DROP CONSTRAINT ON (n:KBNode) ASSERT n.node_id IS UNIQUE")
                            logger.info(
                                "Memgraph: dropped legacy (node_id) unique constraint "
                                "— it conflicts with multi-workspace tenancy."
                            )
                        except Exception as exc:
                            logger.warning("Memgraph: could not drop legacy constraint: %s", exc)
            except Exception as exc:
                logger.debug("Memgraph: SHOW CONSTRAINT INFO failed: %s", exc)

            # 2. Create indexes to speed up MERGE / MATCH by (workspace_id, node_id).
            # Memgraph's standard CONSTRAINT grammar doesn't support composite
            # uniqueness; we rely on the MERGE pattern and per-workspace
            # clear_workspace() for isolation.
            for idx_stmt in (
                "CREATE INDEX ON :KBNode(workspace_id)",
                "CREATE INDEX ON :KBNode(node_id)",
            ):
                try:
                    s.run(idx_stmt)
                except Exception as exc:
                    logger.debug("index stmt skipped: %s (%s)", idx_stmt, exc)
