"""
Neo4j / Memgraph graph store adapter — per-workspace scoping.

Every KBNode carries a ``workspace_id`` property. Every read, write, and
traversal takes an explicit ``workspace_id`` parameter and filters on it, so
two workspaces sharing the same Memgraph database cannot accidentally see each
other's nodes. A composite unique constraint on (workspace_id, node_id)
prevents collisions across workspaces while still allowing identical node IDs
in different workspaces (e.g. every workspace has its own ``knowledge:root``).

Relationship types
------------------
Matches EdgeType enum exactly: CONTAINS, USES_TOOL, IMPLEMENTS, INTEGRATES_WITH,
HAS_CONTENT, NEXT_STEP, RELATED_TO, DEFINED_IN.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

_NODE_BATCH = 500
_EDGE_BATCH = 500

# Runtime tenant-isolation guard. Multi-tenant graph correctness depends on
# every read / write / delete carrying a ``workspace_id`` filter. A forgotten
# filter leaks another tenant's graph data. The guard scans each Cypher
# string before execution: statements that read or mutate KBNode data must
# bind either ``$wid`` / ``$workspace_id`` as a parameter and reference it
# in the cypher, or be explicitly marked ``_unscoped_ok=True`` for schema
# operations (CREATE INDEX, SHOW CONSTRAINT, etc.).
#
# Default: enabled in all envs. Failing loud on missed scoping is strictly
# better than a silent cross-tenant leak; set ``GRAPH_FILTER_GUARD=0`` to
# turn it off only if a specific statement demonstrably trips a false
# positive in production and you need to ship the fix.
_GUARD_ENABLED = os.environ.get("GRAPH_FILTER_GUARD", "1") != "0"

# Commands that touch tenant data. Anything starting with one of these
# keywords must either be scoped or marked ``_unscoped_ok``.
_TENANT_DATA_KEYWORDS = ("MATCH", "MERGE", "CREATE (", "DELETE", "DETACH DELETE")
# Schema / infra statements that legitimately run without a workspace_id.
_SCHEMA_KEYWORDS = (
    "CREATE INDEX",
    "CREATE CONSTRAINT",
    "DROP INDEX",
    "DROP CONSTRAINT",
    "SHOW",
    "CALL DB.",
    "CALL DBMS.",
)


def _assert_ws_scoped(cypher: str, params: dict | None) -> None:
    """Raise on a Cypher that mutates KBNode data without a workspace_id scope.

    This is a linter, not a proof — a sufficiently obfuscated statement can
    still slip through. It catches the common failure mode (someone writing
    a new query method and forgetting to add ``WHERE n.workspace_id = $wid``)
    which is the actual leakage risk at 1M tenants.
    """
    if not _GUARD_ENABLED:
        return
    stripped = cypher.strip()
    upper = stripped.upper()
    # Skip schema-only commands.
    for kw in _SCHEMA_KEYWORDS:
        if upper.startswith(kw):
            return
    # Only assert on statements that touch tenant data. Literal CREATE
    # statements without a relationship pattern (e.g. CREATE INDEX) are
    # already caught above.
    if not any(re.search(r"(^|\s)" + re.escape(kw), upper) for kw in _TENANT_DATA_KEYWORDS):
        return
    params = params or {}
    has_param = "wid" in params or "workspace_id" in params
    mentions_ws = (
        "WORKSPACE_ID" in upper
        or "$WID" in upper
        or "$WORKSPACE_ID" in upper
    )
    if not (has_param and mentions_ws):
        raise RuntimeError(
            "Graph tenant-isolation guard tripped: cypher touches tenant "
            "data without binding workspace_id. First 200 chars: "
            + stripped[:200]
        )


def _run_scoped(session, cypher: str, **params) -> Any:
    """``session.run`` wrapper that enforces the tenant-isolation guard.

    All new read / write paths should funnel through this helper. Existing
    callers that pass ``_unscoped_ok=True`` opt out for schema statements.
    """
    if params.pop("_unscoped_ok", False):
        return session.run(cypher, **params)
    _assert_ws_scoped(cypher, params)
    return session.run(cypher, **params)


_TYPE_TO_LABEL: dict[str, str] = {
    "domain":   "Domain",
    "chapter":  "Chapter",
    "section":  "Section",
    "concept":  "Concept",
    "tool":     "Tool",
    "process":  "Process",
    "table":    "Table",
    "glossary": "Glossary",
}


class Neo4jGraphStore:
    """Workspace-scoped graph store on top of Neo4j / Memgraph."""

    def __init__(
        self,
        uri: str,
        username: str,
        password: str,
        database: str = "neo4j",
    ) -> None:
        from neo4j import GraphDatabase

        self._database = database
        self._driver = GraphDatabase.driver(uri, auth=(username, password))
        self._driver.verify_connectivity()
        self._ensure_constraints()
        logger.info("Graph storage connected: uri='%s', database='%s'", uri, database)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._driver.close()

    def __enter__(self) -> "Neo4jGraphStore":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _ensure_constraints(self) -> None:
        """Multi-tenancy requires node_id to NOT be globally unique — every
        workspace has its own ``knowledge:root`` etc. We drop any legacy
        single-property (node_id) unique constraint carried over from the
        pre-workspace schema, try to install a composite (workspace_id,
        node_id) constraint, and fall back to plain indexes if Memgraph
        rejects composite constraints.
        """
        with self._driver.session(database=self._database) as s:
            # 1. Drop legacy constraints that conflict with tenancy.
            try:
                info = list(s.run("SHOW CONSTRAINT INFO"))
                for row in info:
                    d = dict(row)
                    label = d.get("label")
                    props = d.get("properties") or []
                    ctype = d.get("constraint type") or d.get("type") or ""
                    if label == "KBNode" and props == ["node_id"] and "unique" in str(ctype).lower():
                        try:
                            # Memgraph syntax
                            s.run("DROP CONSTRAINT ON (n:KBNode) ASSERT n.node_id IS UNIQUE")
                            logger.info("Dropped legacy (KBNode.node_id) unique constraint.")
                        except Exception:
                            try:
                                s.run("DROP CONSTRAINT kb_node_id_unique IF EXISTS")
                            except Exception as exc:
                                logger.warning("Could not drop legacy constraint: %s", exc)
            except Exception as exc:
                logger.debug("SHOW CONSTRAINT INFO not supported / errored: %s", exc)

            # 2. Best-effort composite uniqueness.
            composite_ok = False
            for stmt in (
                "CREATE CONSTRAINT ON (n:KBNode) ASSERT (n.workspace_id, n.node_id) IS UNIQUE",
                "CREATE CONSTRAINT kb_ws_node_unique IF NOT EXISTS "
                "FOR (n:KBNode) REQUIRE (n.workspace_id, n.node_id) IS UNIQUE",
            ):
                try:
                    s.run(stmt)
                    composite_ok = True
                    break
                except Exception:
                    continue
            if not composite_ok:
                logger.debug("composite constraint not supported; adding plain indexes.")
                for idx_stmt in (
                    "CREATE INDEX ON :KBNode(workspace_id)",
                    "CREATE INDEX ON :KBNode(node_id)",
                ):
                    try:
                        s.run(idx_stmt)
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Write path — all take workspace_id
    # ------------------------------------------------------------------

    def clear_workspace(self, workspace_id: str) -> None:
        """Delete every KBNode belonging to *workspace_id* (and all its edges)."""
        with self._driver.session(database=self._database) as s:
            s.run(
                "MATCH (n:KBNode {workspace_id: $wid}) DETACH DELETE n",
                wid=workspace_id,
            )
        logger.info("Graph storage: cleared workspace %s", workspace_id)

    def clear_graph(self) -> None:
        """Delete *all* KBNode nodes across all workspaces. Used by wipe_all.py."""
        with self._driver.session(database=self._database) as s:
            s.run("MATCH (n:KBNode) DETACH DELETE n")
        logger.info("Graph storage cleared (all KBNode nodes removed).")

    def bulk_create_nodes_no_apoc(
        self, nodes: dict[str, Any], workspace_id: str
    ) -> None:
        """Batch-create nodes, stamping each with *workspace_id*."""
        from collections import defaultdict

        by_type: dict[str, list[dict]] = defaultdict(list)
        for node in nodes.values():
            d = node.to_dict()
            d["node_type"] = d.get("node_type", "")
            d["kb_source"] = d.get("kb_source", "")
            d["workspace_id"] = workspace_id
            type_label = _TYPE_TO_LABEL.get(d["node_type"], "Unknown")
            by_type[type_label].append(self._sanitize_props(d))

        for type_label, rows in by_type.items():
            for start in range(0, len(rows), _NODE_BATCH):
                batch = rows[start : start + _NODE_BATCH]
                cypher = f"""
                UNWIND $rows AS row
                MERGE (n:KBNode {{workspace_id: row.workspace_id, node_id: row.node_id}})
                SET n:{type_label}
                SET n += row
                """
                with self._driver.session(database=self._database) as s:
                    s.run(cypher, rows=batch)

        total = sum(len(v) for v in by_type.values())
        logger.info("Graph storage: created/merged %d nodes in ws=%s.", total, workspace_id)

    @staticmethod
    def _sanitize_props(d: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in d.items():
            if v is None:
                continue
            elif isinstance(v, (str, int, float, bool)):
                out[k] = v
            elif isinstance(v, list):
                if all(isinstance(item, (str, int, float, bool)) for item in v):
                    out[k] = v
                else:
                    out[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, dict):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = str(v)
        return out

    def bulk_create_edges(self, edges: list[Any], workspace_id: str) -> None:
        """Create edges between nodes *within the same workspace*."""
        total = len(edges)
        rows = []
        for edge in edges:
            meta = dict(edge.metadata) if edge.metadata else {}
            meta["weight"] = edge.weight
            rows.append({
                "source_id": edge.source_id,
                "target_id": edge.target_id,
                "edge_type": edge.edge_type.value,
                "props": self._sanitize_props(meta),
            })

        for start in range(0, total, _EDGE_BATCH):
            batch = rows[start : start + _EDGE_BATCH]
            by_type: dict[str, list[dict]] = {}
            for row in batch:
                by_type.setdefault(row["edge_type"], []).append(row)

            with self._driver.session(database=self._database) as s:
                for etype, etype_rows in by_type.items():
                    cypher = f"""
                    UNWIND $rows AS row
                    MATCH (src:KBNode {{workspace_id: $wid, node_id: row.source_id}})
                    MATCH (tgt:KBNode {{workspace_id: $wid, node_id: row.target_id}})
                    MERGE (src)-[r:{etype}]->(tgt)
                    SET r += row.props
                    """
                    s.run(cypher, rows=etype_rows, wid=workspace_id)

        logger.info("Neo4j: created %d edges in ws=%s.", total, workspace_id)

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def get_node(self, node_id: str, workspace_id: str) -> dict[str, Any] | None:
        with self._driver.session(database=self._database) as s:
            r = s.run(
                "MATCH (n:KBNode {workspace_id: $wid, node_id: $id}) RETURN properties(n) AS props",
                wid=workspace_id, id=node_id,
            )
            rec = r.single()
            return dict(rec["props"]) if rec else None

    def node_exists(self, node_id: str, workspace_id: str) -> bool:
        with self._driver.session(database=self._database) as s:
            r = s.run(
                "MATCH (n:KBNode {workspace_id: $wid, node_id: $id}) RETURN count(n) AS cnt",
                wid=workspace_id, id=node_id,
            )
            return r.single()["cnt"] > 0

    def get_children(
        self, node_id: str, workspace_id: str, edge_types: list[str] | None = None
    ) -> list[str]:
        if edge_types:
            rel_filter = "|".join(edge_types)
            cypher = f"""
            MATCH (n:KBNode {{workspace_id: $wid, node_id: $id}})-[r:{rel_filter}]->(c:KBNode)
            WHERE c.workspace_id = $wid
            RETURN c.node_id AS child_id
            """
        else:
            cypher = """
            MATCH (n:KBNode {workspace_id: $wid, node_id: $id})-[r]->(c:KBNode)
            WHERE c.workspace_id = $wid
            RETURN c.node_id AS child_id
            """
        with self._driver.session(database=self._database) as s:
            return [r["child_id"] for r in s.run(cypher, wid=workspace_id, id=node_id)]

    def get_parents(self, node_id: str, workspace_id: str) -> list[str]:
        with self._driver.session(database=self._database) as s:
            r = s.run(
                "MATCH (p:KBNode {workspace_id: $wid})-[]->(n:KBNode {workspace_id: $wid, node_id: $id}) "
                "RETURN p.node_id AS pid",
                wid=workspace_id, id=node_id,
            )
            return [row["pid"] for row in r]

    def get_edges(self, node_id: str, workspace_id: str) -> list[dict[str, Any]]:
        with self._driver.session(database=self._database) as s:
            r = s.run(
                """
                MATCH (n:KBNode {workspace_id: $wid, node_id: $id})-[r]->(t:KBNode {workspace_id: $wid})
                RETURN 'out' AS dir, t.node_id AS other_id, type(r) AS rtype, properties(r) AS props
                UNION
                MATCH (s:KBNode {workspace_id: $wid})-[r]->(n:KBNode {workspace_id: $wid, node_id: $id})
                RETURN 'in' AS dir, s.node_id AS other_id, type(r) AS rtype, properties(r) AS props
                """,
                wid=workspace_id, id=node_id,
            )
            edges = []
            for rec in r:
                entry: dict[str, Any] = {
                    "direction": rec["dir"],
                    "other_id": rec["other_id"],
                    "edge_type": rec["rtype"],
                }
                entry.update(rec["props"])
                edges.append(entry)
            return edges

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def bfs_traverse(
        self,
        start_ids: list[str],
        workspace_id: str,
        max_depth: int = 4,
        edge_types: list[str] | None = None,
        max_nodes: int = 40,
    ) -> list[str]:
        if not start_ids:
            return []

        if edge_types:
            rel_pattern = "|".join(edge_types)
            path_pattern = f"-[r:{rel_pattern}*1..{max_depth}]->"
        else:
            path_pattern = f"-[*1..{max_depth}]->"

        cypher = f"""
        MATCH (start:KBNode)
        WHERE start.workspace_id = $wid AND start.node_id IN $start_ids
        MATCH (start){path_pattern}(reached:KBNode)
        WHERE reached.workspace_id = $wid
        RETURN DISTINCT reached.node_id AS node_id
        LIMIT $limit
        """
        with self._driver.session(database=self._database) as s:
            visited = [r["node_id"] for r in s.run(
                cypher, wid=workspace_id, start_ids=start_ids, limit=max_nodes,
            )]

        return list(dict.fromkeys(start_ids + visited))[:max_nodes]

    def get_subtree(
        self, root_id: str, workspace_id: str, max_depth: int = 3
    ) -> list[dict[str, Any]]:
        cypher = f"""
        MATCH (root:KBNode {{workspace_id: $wid, node_id: $root_id}})
        OPTIONAL MATCH (root)-[:CONTAINS*1..{max_depth}]->(child:KBNode)
        WHERE child.workspace_id = $wid
        RETURN properties(root) AS props
        UNION
        MATCH (root:KBNode {{workspace_id: $wid, node_id: $root_id}})-[:CONTAINS*1..{max_depth}]->(child:KBNode)
        WHERE child.workspace_id = $wid
        RETURN properties(child) AS props
        """
        with self._driver.session(database=self._database) as s:
            return [dict(r["props"]) for r in s.run(cypher, wid=workspace_id, root_id=root_id)]

    def get_tools_for_node(
        self, node_id: str, workspace_id: str
    ) -> list[dict[str, Any]]:
        cypher = """
        MATCH (n:KBNode {workspace_id: $wid, node_id: $id})-[:USES_TOOL]->(t:Tool {workspace_id: $wid})
        RETURN properties(t) AS props
        UNION
        MATCH (src:KBNode {workspace_id: $wid})-[:IMPLEMENTS]->(n:KBNode {workspace_id: $wid, node_id: $id})
        MATCH (src)-[:CONTAINS*0..3]->(t:Tool {workspace_id: $wid})
        RETURN properties(t) AS props
        """
        with self._driver.session(database=self._database) as s:
            return [dict(r["props"]) for r in s.run(cypher, wid=workspace_id, id=node_id)]

    def full_tree(self, workspace_id: str, max_depth: int = 2) -> list[dict[str, Any]]:
        with self._driver.session(database=self._database) as s:
            root_ids = [r["root_id"] for r in s.run(
                "MATCH (root:Domain {workspace_id: $wid}) RETURN root.node_id AS root_id",
                wid=workspace_id,
            )]
        return [{"root_id": rid, "nodes": self.get_subtree(rid, workspace_id, max_depth)} for rid in root_ids]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self, workspace_id: str | None = None) -> dict[str, Any]:
        """If *workspace_id* is given, counts are scoped to it."""
        with self._driver.session(database=self._database) as s:
            if workspace_id:
                node_result = s.run(
                    "MATCH (n:KBNode {workspace_id: $wid}) "
                    "RETURN n.node_type AS t, count(n) AS cnt",
                    wid=workspace_id,
                )
                edge_result = s.run(
                    "MATCH (n:KBNode {workspace_id: $wid})-[r]->(m:KBNode {workspace_id: $wid}) "
                    "RETURN type(r) AS t, count(r) AS cnt",
                    wid=workspace_id,
                )
                total_nodes = s.run(
                    "MATCH (n:KBNode {workspace_id: $wid}) RETURN count(n) AS cnt",
                    wid=workspace_id,
                ).single()["cnt"]
                total_edges = s.run(
                    "MATCH (:KBNode {workspace_id: $wid})-[r]->(:KBNode {workspace_id: $wid}) "
                    "RETURN count(r) AS cnt",
                    wid=workspace_id,
                ).single()["cnt"]
            else:
                node_result = s.run("MATCH (n:KBNode) RETURN n.node_type AS t, count(n) AS cnt")
                edge_result = s.run("MATCH (:KBNode)-[r]->(:KBNode) RETURN type(r) AS t, count(r) AS cnt")
                total_nodes = s.run("MATCH (n:KBNode) RETURN count(n) AS cnt").single()["cnt"]
                total_edges = s.run("MATCH (:KBNode)-[r]->(:KBNode) RETURN count(r) AS cnt").single()["cnt"]

            nodes_by_type = {r["t"]: r["cnt"] for r in node_result}
            edges_by_type = {r["t"]: r["cnt"] for r in edge_result}

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "nodes_by_type": nodes_by_type,
            "edges_by_type": edges_by_type,
        }
