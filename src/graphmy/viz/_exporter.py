"""
graphmy/viz/_exporter.py
=========================
Converts a GraphStore into the cytoscape.js JSON format for visualisation.

Cytoscape.js expects:
  {
    "elements": {
      "nodes": [{"data": {"id": ..., "label": ..., ...}}, ...],
      "edges": [{"data": {"id": ..., "source": ..., "target": ..., "kind": ...}}, ...]
    }
  }

We also produce a flat ``{"nodes": [...], "edges": [...]}`` format (cytoscape.js
accepts both) which is simpler to work with in the HTML template.

Node data fields exported (all used by the detail panel in the HTML template):
  id, label, kind, language, file, line, end_line, qualified,
  docstring, signature, body, is_async, decorators

Edge data fields:
  id (unique), source, target, kind

External nodes (kind="external") are included in the graph but styled
differently in the template (dashed border, grey, no detail panel).
"""

from __future__ import annotations

from typing import Any

from graphmy.graph._model import SymbolKind
from graphmy.graph._store import GraphStore


def export_cytoscape(graph: GraphStore) -> dict[str, Any]:
    """
    Convert the full GraphStore into a cytoscape.js-compatible JSON dict.

    Parameters
    ----------
    graph : GraphStore
        The knowledge graph to export.

    Returns
    -------
    dict
        A dict with two keys:
          ``"nodes"`` — list of cytoscape node element dicts
          ``"edges"`` — list of cytoscape edge element dicts

    The returned dict can be JSON-serialised and passed directly to
    ``cytoscape({ elements: ... })``.
    """
    cy_nodes: list[dict[str, Any]] = []
    cy_edges: list[dict[str, Any]] = []

    # Export all nodes.
    for node in graph.all_nodes():
        cy_nodes.append(_node_to_cy(node))

    # Export all edges. We generate a unique edge ID from source + target + kind
    # so the template can reference individual edges if needed.
    edge_id_counter: dict[str, int] = {}
    for src, tgt, attrs in graph.graph.edges(data=True):
        kind = attrs.get("kind", "UNKNOWN")
        base_id = f"{src}--{kind}--{tgt}"
        count = edge_id_counter.get(base_id, 0)
        edge_id_counter[base_id] = count + 1
        edge_id = base_id if count == 0 else f"{base_id}_{count}"

        cy_edges.append(
            {
                "data": {
                    "id": edge_id,
                    "source": src,
                    "target": tgt,
                    "kind": kind,
                }
            }
        )

    return {"nodes": cy_nodes, "edges": cy_edges}


def export_cytoscape_subgraph(
    graph: GraphStore,
    node_ids: list[str],
    hops: int = 1,
) -> dict[str, Any]:
    """
    Export a subgraph centred on the given node_ids.

    Includes the seed nodes plus all nodes reachable within ``hops`` steps
    in either direction (predecessors and successors). Useful for the viz
    server's search results view — shows context without the full graph.

    Parameters
    ----------
    graph : GraphStore
    node_ids : list[str]
        The seed node IDs to start from.
    hops : int
        Number of hops to expand. Default 1 (immediate neighbours only).

    Returns
    -------
    dict
        Same format as ``export_cytoscape()``.
    """
    g = graph.graph

    # BFS to collect all nodes within `hops` steps.
    frontier: set[str] = set(node_ids)
    visited: set[str] = set(node_ids)

    for _ in range(hops):
        next_frontier: set[str] = set()
        for nid in frontier:
            if g.has_node(nid):
                next_frontier.update(g.predecessors(nid))
                next_frontier.update(g.successors(nid))
        new_nodes = next_frontier - visited
        visited.update(new_nodes)
        frontier = new_nodes

    # Build cytoscape elements only for the visited subset.
    cy_nodes: list[dict[str, Any]] = []
    for nid in visited:
        node = graph.get_node(nid)
        if node:
            cy_nodes.append(_node_to_cy(node))

    cy_edges: list[dict[str, Any]] = []
    edge_id_counter: dict[str, int] = {}
    for src, tgt, attrs in g.edges(data=True):
        if src not in visited or tgt not in visited:
            continue
        kind = attrs.get("kind", "UNKNOWN")
        base_id = f"{src}--{kind}--{tgt}"
        count = edge_id_counter.get(base_id, 0)
        edge_id_counter[base_id] = count + 1
        edge_id = base_id if count == 0 else f"{base_id}_{count}"
        cy_edges.append(
            {
                "data": {
                    "id": edge_id,
                    "source": src,
                    "target": tgt,
                    "kind": kind,
                }
            }
        )

    return {"nodes": cy_nodes, "edges": cy_edges}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _node_to_cy(node: Any) -> dict[str, Any]:
    """
    Convert a SymbolNode to a cytoscape.js node element dict.

    The ``data`` object holds all fields we want accessible in the
    detail panel and in cytoscape style selectors.
    """
    # Determine if this is a "ghost" node (added by networkx from an edge
    # reference but never given attributes). Ghost nodes have no kind.
    kind_val = node.kind.value if hasattr(node.kind, "value") else str(node.kind)
    is_external = kind_val == SymbolKind.EXTERNAL.value

    return {
        "data": {
            "id": node.node_id,
            # Display label: short name for most nodes, filename for FILE nodes.
            "label": node.name,
            "kind": kind_val,
            "language": node.language,
            "file": node.file,
            "line": node.line,
            "end_line": node.end_line,
            "qualified": node.qualified,
            "docstring": node.docstring,
            "signature": node.signature,
            # Full body is included for the source preview in the detail panel.
            # It may be large — the HTML template uses a <pre> with overflow: auto.
            "body": node.body,
            "is_async": node.is_async,
            "decorators": node.decorators,
        },
        # cytoscape.js "classes" drive CSS styling.
        # Multiple space-separated classes can be set.
        "classes": _node_classes(kind_val, is_external),
    }


def _node_classes(kind: str, is_external: bool) -> str:
    """
    Return a space-separated string of CSS class names for a node.

    These map to style rules in the HTML template:
      kind-file, kind-class, kind-function, kind-method,
      kind-interface, kind-struct, kind-enum, kind-trait,
      kind-external
    """
    classes = [f"kind-{kind}"]
    if is_external:
        classes.append("external")
    return " ".join(classes)
