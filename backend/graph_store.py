"""
Neo4j knowledge graph of the system's variables and how they correlate.

The graph models the physical system as:

    (:Parameter {id, label, unit, mean, min, max, std})
        -[:CORRELATES_WITH {r, abs_r, strength, direction, r_squared}]->
    (:Parameter)

Why a graph and not just a table: correlations form a network, not a list.
Seeing that Light drives Power, Power drives Battery Voltage, and Temperature
degrades Power -- all at once, with edge weight showing strength -- makes the
physics legible in a way a 7x7 matrix of numbers does not.

Neo4j is OPTIONAL. If NEO4J_URI is blank or the server is unreachable, the same
data is computed with pandas and returned in an identical shape, so the
dashboard works either way. Set it up when you want the real thing:
free cloud instance at https://console.neo4j.io (AuraDB Free).
"""
from typing import Dict, List, Optional

from . import config as C
from .analytics import correlations

_driver = None
_status = {"connected": False, "reason": "not attempted", "uri": C.NEO4J_URI or None}


def _get_driver():
    global _driver
    if _driver is not None:
        return _driver
    if not C.NEO4J_URI:
        _status.update(connected=False,
                       reason="NEO4J_URI is blank -- using the pandas fallback")
        return None
    try:
        from neo4j import GraphDatabase
        _driver = GraphDatabase.driver(C.NEO4J_URI,
                                       auth=(C.NEO4J_USER, C.NEO4J_PASSWORD))
        _driver.verify_connectivity()
        _status.update(connected=True, reason="connected")
        return _driver
    except Exception as exc:
        _status.update(connected=False, reason=f"{type(exc).__name__}: {exc}")
        _driver = None
        return None


def status() -> Dict:
    _get_driver()
    return dict(_status)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
SCHEMA_CYPHER = """
CREATE CONSTRAINT param_id IF NOT EXISTS
FOR (p:Parameter) REQUIRE p.id IS UNIQUE
"""

UPSERT_NODE = """
MERGE (p:Parameter {id: $id})
SET p.label = $label, p.unit = $unit, p.mean = $mean,
    p.min = $min, p.max = $max, p.std = $std, p.samples = $samples
"""

UPSERT_EDGE = """
MATCH (a:Parameter {id: $source}), (b:Parameter {id: $target})
MERGE (a)-[c:CORRELATES_WITH]->(b)
SET c.r = $r, c.abs_r = $abs_r, c.strength = $strength,
    c.direction = $direction, c.r_squared = $r_squared,
    c.interpretation = $interpretation
"""


def sync(days: int = 14) -> Dict:
    """Recompute correlations and push the whole graph into Neo4j."""
    data = correlations(days=days)
    if data.get("error"):
        return {"synced": False, "reason": data["error"]}

    driver = _get_driver()
    if driver is None:
        return {"synced": False, "reason": _status["reason"],
                "nodes": len(data["nodes"]), "edges": len(data["edges"]),
                "fallback": "pandas"}
    try:
        with driver.session() as session:
            session.run(SCHEMA_CYPHER)
            session.run("MATCH (:Parameter)-[c:CORRELATES_WITH]->() DELETE c")
            for node in data["nodes"]:
                session.run(UPSERT_NODE, **node)
            for edge in data["edges"]:
                session.run(UPSERT_EDGE, **edge)
        return {"synced": True, "nodes": len(data["nodes"]),
                "edges": len(data["edges"]), "uri": C.NEO4J_URI}
    except Exception as exc:
        _status.update(connected=False, reason=str(exc))
        return {"synced": False, "reason": str(exc), "fallback": "pandas"}


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
READ_GRAPH = """
MATCH (a:Parameter)
OPTIONAL MATCH (a)-[c:CORRELATES_WITH]->(b:Parameter)
RETURN a.id AS id, a.label AS label, a.unit AS unit, a.mean AS mean,
       a.min AS min, a.max AS max, a.std AS std, a.samples AS samples,
       collect(CASE WHEN b IS NULL THEN NULL ELSE {
         target: b.id, r: c.r, abs_r: c.abs_r, strength: c.strength,
         direction: c.direction, r_squared: c.r_squared,
         interpretation: c.interpretation } END) AS rels
"""


def get_graph(days: int = 14, refresh: bool = False) -> Dict:
    """
    Return {nodes, edges, source} for the dashboard.
    Reads from Neo4j when available, otherwise computes with pandas.
    """
    driver = _get_driver()
    if driver is not None:
        if refresh:
            sync(days=days)
        try:
            nodes, edges = [], []
            with driver.session() as session:
                for rec in session.run(READ_GRAPH):
                    nodes.append({k: rec[k] for k in
                                  ("id", "label", "unit", "mean", "min", "max",
                                   "std", "samples")})
                    for rel in rec["rels"]:
                        if rel:
                            edges.append({"source": rec["id"], **rel})
            if nodes:
                fallback = correlations(days=days)
                return {"nodes": nodes, "edges": edges, "source": "neo4j",
                        "table": fallback.get("table"),
                        "formulas": fallback.get("formulas", []),
                        "notes": fallback.get("notes", []),
                        "cypher": READ_GRAPH.strip()}
        except Exception as exc:
            _status.update(connected=False, reason=str(exc))

    data = correlations(days=days)
    data["source"] = "pandas"
    data["cypher"] = READ_GRAPH.strip()
    data["neo4j_status"] = _status["reason"]
    return data


def query_strongest(top: int = 5, days: int = 14) -> Dict:
    """The strongest relationships -- used by the agent's correlation tool."""
    graph = get_graph(days=days)
    edges = sorted(graph.get("edges", []), key=lambda e: -abs(e.get("r", 0)))[:top]
    node_labels = {n["id"]: n["label"] for n in graph.get("nodes", [])}
    return {
        "source": graph.get("source", "pandas"),
        "top_relationships": [
            {
                "between": f"{node_labels.get(e['source'], e['source'])} and "
                           f"{node_labels.get(e['target'], e['target'])}",
                "r": e["r"], "strength": e["strength"], "direction": e["direction"],
                "r_squared": e.get("r_squared"),
                "interpretation": e.get("interpretation"),
            } for e in edges
        ],
        "table": graph.get("table"),
        "formulas": graph.get("formulas", []),
        "notes": graph.get("notes", []),
    }
