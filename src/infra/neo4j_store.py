"""
Neo4j graph store adapter.

Provides Neo4jGraphStore — a wrapper around the Neo4j Python driver that
mirrors all graph-traversal methods of the local NetworkX-based KnowledgeGraph.

Node schema
-----------
Every node gets two labels: :KBNode plus its NodeType label (e.g. :Chapter).
A uniqueness constraint on (:KBNode {node_id}) is created on first connect.
All BaseNode.to_dict() fields are stored as node properties.

Relationship types
------------------
Matches EdgeType enum exactly: CONTAINS, USES_TOOL, IMPLEMENTS, INTEGRATES_WITH,
HAS_CONTENT, NEXT_STEP, RELATED_TO, DEFINED_IN.
Each relationship carries `weight` and any extra `metadata` as properties.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_NODE_BATCH   = 500   # nodes per UNWIND batch
_EDGE_BATCH   = 500   # edges per UNWIND batch


# Map NodeType string values to Cypher-safe label names
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
    """
    Neo4j-backed knowledge graph store.

    Parameters
    ----------
    uri      : Bolt/Neo4j URI (e.g. "neo4j+s://…")
    username : Neo4j username
    password : Neo4j password
    database : Database name (defaults to "neo4j")
    """

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
        logger.info(
            f"Neo4jGraphStore connected: uri='{uri}', database='{database}'"
        )

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
    # Schema management
    # ------------------------------------------------------------------

    def _ensure_constraints(self) -> None:
        with self._driver.session(database=self._database) as s:
            s.run(
                "CREATE CONSTRAINT kb_node_id_unique IF NOT EXISTS "
                "FOR (n:KBNode) REQUIRE n.node_id IS UNIQUE"
            )

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def clear_graph(self) -> None:
        """Delete all KBNode nodes and their relationships (clean slate before rebuild)."""
        with self._driver.session(database=self._database) as s:
            s.run("MATCH (n:KBNode) DETACH DELETE n")
        logger.info("Neo4j graph cleared (all KBNode nodes removed).")

    def bulk_create_nodes(self, nodes: dict[str, Any]) -> None:
        """
        Batch-create nodes from a dict of {node_id: BaseNode}.

        Each node receives labels :KBNode and its NodeType label.
        Properties come from BaseNode.to_dict().
        """
        items = list(nodes.values())
        total = len(items)

        for start in range(0, total, _NODE_BATCH):
            batch_nodes = items[start : start + _NODE_BATCH]
            rows = []
            for node in batch_nodes:
                d = node.to_dict()
                d["node_type"]  = d.get("node_type", "")
                d["kb_source"]  = d.get("kb_source", "")
                d["type_label"] = _TYPE_TO_LABEL.get(d["node_type"], "Unknown")
                rows.append(self._sanitize_props(d))

            with self._driver.session(database=self._database) as s:
                s.run(
                    """
                    UNWIND $rows AS row
                    CALL apoc.create.node(['KBNode', row.type_label], row)
                    YIELD node
                    RETURN count(node)
                    """,
                    rows=rows,
                )

        logger.info(f"Neo4j: created {total} nodes.")

    @staticmethod
    def _sanitize_props(d: dict[str, Any]) -> dict[str, Any]:
        """
        Convert any dict/list-of-dict property values to JSON strings.

        Neo4j only supports primitive types and arrays of primitives as
        property values; nested dicts/objects must be serialised to strings.
        """
        out: dict[str, Any] = {}
        for k, v in d.items():
            if v is None:
                # Neo4j does not allow None/null as a property value
                continue
            elif isinstance(v, (str, int, float, bool)):
                out[k] = v
            elif isinstance(v, list):
                # Keep lists of primitives; serialise lists containing dicts
                if all(isinstance(item, (str, int, float, bool)) for item in v):
                    out[k] = v
                else:
                    out[k] = json.dumps(v, ensure_ascii=False)
            elif isinstance(v, dict):
                out[k] = json.dumps(v, ensure_ascii=False)
            else:
                out[k] = str(v)
        return out

    def bulk_create_nodes_no_apoc(self, nodes: dict[str, Any]) -> None:
        """
        Fallback bulk create without APOC (uses per-type queries).

        Groups nodes by NodeType and issues one UNWIND per type so we can
        dynamically apply the second label.
        """
        from collections import defaultdict

        by_type: dict[str, list[dict]] = defaultdict(list)
        for node in nodes.values():
            d = node.to_dict()
            d["node_type"] = d.get("node_type", "")
            d["kb_source"] = d.get("kb_source", "")
            type_label = _TYPE_TO_LABEL.get(d["node_type"], "Unknown")
            by_type[type_label].append(self._sanitize_props(d))

        for type_label, rows in by_type.items():
            for start in range(0, len(rows), _NODE_BATCH):
                batch = rows[start : start + _NODE_BATCH]
                cypher = f"""
                UNWIND $rows AS row
                MERGE (n:KBNode {{node_id: row.node_id}})
                SET n:{type_label}
                SET n += row
                """
                with self._driver.session(database=self._database) as s:
                    s.run(cypher, rows=batch)

        total = sum(len(v) for v in by_type.values())
        logger.info(f"Neo4j (no-APOC): created/merged {total} nodes.")

    def bulk_create_edges(self, edges: list[Any]) -> None:
        """
        Batch-create relationships from a list of Edge objects.

        Uses MATCH on both endpoints and MERGE on the relationship to avoid
        duplicates if called more than once.
        """
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
            # We group by edge_type so we can use a dynamic relationship type.
            # Since pure Cypher can't parameterise rel types, we issue one
            # query per distinct type within the batch.
            by_type: dict[str, list[dict]] = {}
            for row in batch:
                by_type.setdefault(row["edge_type"], []).append(row)

            with self._driver.session(database=self._database) as s:
                for etype, etype_rows in by_type.items():
                    cypher = f"""
                    UNWIND $rows AS row
                    MATCH (src:KBNode {{node_id: row.source_id}})
                    MATCH (tgt:KBNode {{node_id: row.target_id}})
                    MERGE (src)-[r:{etype}]->(tgt)
                    SET r += row.props
                    """
                    s.run(cypher, rows=etype_rows)

        logger.info(f"Neo4j: created {total} edges.")

    # ------------------------------------------------------------------
    # Read path — node lookup
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        with self._driver.session(database=self._database) as s:
            result = s.run(
                "MATCH (n:KBNode {node_id: $id}) RETURN properties(n) AS props",
                id=node_id,
            )
            record = result.single()
            return dict(record["props"]) if record else None

    def node_exists(self, node_id: str) -> bool:
        with self._driver.session(database=self._database) as s:
            result = s.run(
                "MATCH (n:KBNode {node_id: $id}) RETURN count(n) AS cnt",
                id=node_id,
            )
            return result.single()["cnt"] > 0

    def get_children(self, node_id: str, edge_types: list[str] | None = None) -> list[str]:
        if edge_types:
            rel_filter = "|".join(edge_types)
            cypher = f"""
            MATCH (n:KBNode {{node_id: $id}})-[r:{rel_filter}]->(c:KBNode)
            RETURN c.node_id AS child_id
            """
        else:
            cypher = """
            MATCH (n:KBNode {node_id: $id})-[r]->(c:KBNode)
            RETURN c.node_id AS child_id
            """
        with self._driver.session(database=self._database) as s:
            result = s.run(cypher, id=node_id)
            return [r["child_id"] for r in result]

    def get_parents(self, node_id: str) -> list[str]:
        with self._driver.session(database=self._database) as s:
            result = s.run(
                "MATCH (p:KBNode)-[]->(n:KBNode {node_id: $id}) RETURN p.node_id AS pid",
                id=node_id,
            )
            return [r["pid"] for r in result]

    def get_edges(self, node_id: str) -> list[dict[str, Any]]:
        with self._driver.session(database=self._database) as s:
            result = s.run(
                """
                MATCH (n:KBNode {node_id: $id})-[r]->(t:KBNode)
                RETURN 'out' AS dir, t.node_id AS other_id, type(r) AS rtype, properties(r) AS props
                UNION
                MATCH (s:KBNode)-[r]->(n:KBNode {node_id: $id})
                RETURN 'in' AS dir, s.node_id AS other_id, type(r) AS rtype, properties(r) AS props
                """,
                id=node_id,
            )
            edges = []
            for record in result:
                entry: dict[str, Any] = {
                    "direction": record["dir"],
                    "other_id": record["other_id"],
                    "edge_type": record["rtype"],
                }
                entry.update(record["props"])
                edges.append(entry)
            return edges

    # ------------------------------------------------------------------
    # Traversal
    # ------------------------------------------------------------------

    def bfs_traverse(
        self,
        start_ids: list[str],
        max_depth: int = 4,
        edge_types: list[str] | None = None,
        max_nodes: int = 40,
    ) -> list[str]:
        """
        BFS traversal using Cypher variable-length paths.

        Returns an ordered list of node_ids reachable within max_depth hops.
        """
        if not start_ids:
            return []

        if edge_types:
            rel_pattern = "|".join(edge_types)
            path_pattern = f"-[r:{rel_pattern}*1..{max_depth}]->"
        else:
            path_pattern = f"-[*1..{max_depth}]->"

        cypher = f"""
        MATCH (start:KBNode)
        WHERE start.node_id IN $start_ids
        MATCH (start){path_pattern}(reached:KBNode)
        RETURN DISTINCT reached.node_id AS node_id
        LIMIT $limit
        """
        with self._driver.session(database=self._database) as s:
            result = s.run(cypher, start_ids=start_ids, limit=max_nodes)
            visited = [r["node_id"] for r in result]

        # Prepend start_ids that exist in the graph (matching BFS semantics)
        all_ids = list(dict.fromkeys(start_ids + visited))
        return all_ids[:max_nodes]

    def get_subtree(self, root_id: str, max_depth: int = 3) -> list[dict[str, Any]]:
        """Return all nodes reachable via CONTAINS from root_id."""
        cypher = f"""
        MATCH (root:KBNode {{node_id: $root_id}})
        OPTIONAL MATCH (root)-[:CONTAINS*1..{max_depth}]->(child:KBNode)
        RETURN properties(root) AS props
        UNION
        MATCH (root:KBNode {{node_id: $root_id}})-[:CONTAINS*1..{max_depth}]->(child:KBNode)
        RETURN properties(child) AS props
        """
        with self._driver.session(database=self._database) as s:
            result = s.run(cypher, root_id=root_id)
            return [dict(r["props"]) for r in result]

    def get_tools_for_node(self, node_id: str) -> list[dict[str, Any]]:
        """Return ToolNode dicts reachable via USES_TOOL or IMPLEMENTS edges."""
        cypher = """
        MATCH (n:KBNode {node_id: $id})-[:USES_TOOL]->(t:Tool)
        RETURN properties(t) AS props
        UNION
        MATCH (src:KBNode)-[:IMPLEMENTS]->(n:KBNode {node_id: $id})
        MATCH (src)-[:CONTAINS*0..3]->(t:Tool)
        RETURN properties(t) AS props
        """
        with self._driver.session(database=self._database) as s:
            result = s.run(cypher, id=node_id)
            return [dict(r["props"]) for r in result]

    def full_tree(self, max_depth: int = 2) -> list[dict[str, Any]]:
        """Return domain roots and their subtrees for UI navigation."""
        cypher = f"""
        MATCH (root:Domain)
        RETURN root.node_id AS root_id
        """
        with self._driver.session(database=self._database) as s:
            root_ids = [r["root_id"] for r in s.run(cypher)]
        return [{"root_id": rid, "nodes": self.get_subtree(rid, max_depth)} for rid in root_ids]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        with self._driver.session(database=self._database) as s:
            node_result = s.run(
                "MATCH (n:KBNode) RETURN n.node_type AS t, count(n) AS cnt"
            )
            nodes_by_type = {r["t"]: r["cnt"] for r in node_result}

            edge_result = s.run(
                "MATCH (:KBNode)-[r]->(:KBNode) RETURN type(r) AS t, count(r) AS cnt"
            )
            edges_by_type = {r["t"]: r["cnt"] for r in edge_result}

            total_nodes = s.run("MATCH (n:KBNode) RETURN count(n) AS cnt").single()["cnt"]
            total_edges = s.run("MATCH (:KBNode)-[r]->(:KBNode) RETURN count(r) AS cnt").single()["cnt"]

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "nodes_by_type": nodes_by_type,
            "edges_by_type": edges_by_type,
        }
