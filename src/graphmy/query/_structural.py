"""
graphmy/query/_structural.py
=============================
Deterministic graph-traversal queries over the knowledge graph.

These queries do NOT require the embedding model. They operate purely on the
graph structure — edges and node attributes — using networkx traversal.

Available queries:
  - ``callers(graph, node_id)``        → all nodes with a CALLS edge to node_id
  - ``callees(graph, node_id)``        → all nodes that node_id calls
  - ``subclasses(graph, node_id)``     → all classes inheriting from node_id
  - ``superclasses(graph, node_id)``   → all ancestors of node_id in the hierarchy
  - ``implementors(graph, node_id)``   → all classes implementing interface node_id
  - ``call_chain(graph, from_id, to_id)`` → shortest CALLS path between two nodes
  - ``imports_of(graph, file_path)``   → all files/modules imported by a file
  - ``find_symbol(graph, name)``       → find all nodes matching a short name

All functions return structured result dicts so they can be rendered in
both the CLI and the web UI without any UI-specific code in this layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from graphmy.graph._model import EdgeKind, SymbolNode
from graphmy.graph._store import GraphStore

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class StructuralResult:
    """
    The result of a structural graph query.

    Fields
    ------
    query_type : str
        Short label for the query (e.g. "callers", "call_chain").
    subject : SymbolNode | None
        The symbol that was the subject of the query (if resolved).
    nodes : list[SymbolNode]
        The symbols returned by the query.
    path : list[SymbolNode]
        For path queries (call_chain), the ordered list of nodes on the path.
    message : str
        Human-readable summary (used in CLI output).
    """

    query_type: str
    subject: SymbolNode | None = None
    nodes: list[SymbolNode] = field(default_factory=list)
    path: list[SymbolNode] = field(default_factory=list)
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict (used by the FastAPI server)."""
        return {
            "query_type": self.query_type,
            "subject": self.subject.to_dict() if self.subject else None,
            "nodes": [n.to_dict() for n in self.nodes],
            "path": [n.to_dict() for n in self.path],
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Public query functions
# ---------------------------------------------------------------------------


def callers(graph: GraphStore, node_id: str) -> StructuralResult:
    """
    Find all symbols that call the given symbol (i.e. have a CALLS → node_id edge).

    Parameters
    ----------
    graph : GraphStore
    node_id : str
        The node_id of the symbol you want to find callers for.

    Returns
    -------
    StructuralResult
        ``.nodes`` contains all callers. ``.subject`` is the queried symbol.
    """
    subject = graph.get_node(node_id)
    caller_nodes = graph.callers(node_id)
    subject_name = subject.name if subject else node_id
    return StructuralResult(
        query_type="callers",
        subject=subject,
        nodes=caller_nodes,
        message=f"{len(caller_nodes)} caller(s) of '{subject_name}'",
    )


def callees(graph: GraphStore, node_id: str) -> StructuralResult:
    """
    Find all symbols called by the given symbol (CALLS edges from node_id).

    Parameters
    ----------
    graph : GraphStore
    node_id : str

    Returns
    -------
    StructuralResult
        ``.nodes`` contains all callees.
    """
    subject = graph.get_node(node_id)
    callee_nodes = graph.callees(node_id)
    subject_name = subject.name if subject else node_id
    return StructuralResult(
        query_type="callees",
        subject=subject,
        nodes=callee_nodes,
        message=f"'{subject_name}' calls {len(callee_nodes)} symbol(s)",
    )


def subclasses(graph: GraphStore, node_id: str) -> StructuralResult:
    """
    Find all classes that inherit from (INHERITS edge to) node_id.

    Parameters
    ----------
    graph : GraphStore
    node_id : str

    Returns
    -------
    StructuralResult
        ``.nodes`` contains all subclasses.
    """
    subject = graph.get_node(node_id)
    sub_nodes = graph.subclasses(node_id)
    subject_name = subject.name if subject else node_id
    return StructuralResult(
        query_type="subclasses",
        subject=subject,
        nodes=sub_nodes,
        message=f"{len(sub_nodes)} subclass(es) of '{subject_name}'",
    )


def superclasses(graph: GraphStore, node_id: str) -> StructuralResult:
    """
    Find all classes that node_id inherits from (INHERITS edges from node_id).

    Parameters
    ----------
    graph : GraphStore
    node_id : str

    Returns
    -------
    StructuralResult
        ``.nodes`` contains the superclass chain (direct parents only at depth 1).
    """
    subject = graph.get_node(node_id)
    super_nodes = graph.superclasses(node_id)
    subject_name = subject.name if subject else node_id
    return StructuralResult(
        query_type="superclasses",
        subject=subject,
        nodes=super_nodes,
        message=f"'{subject_name}' inherits from {len(super_nodes)} class(es)",
    )


def implementors(graph: GraphStore, node_id: str) -> StructuralResult:
    """
    Find all classes that implement (IMPLEMENTS edge to) the given interface.

    Parameters
    ----------
    graph : GraphStore
    node_id : str
        node_id of the interface/protocol.

    Returns
    -------
    StructuralResult
        ``.nodes`` contains all implementors.
    """
    subject = graph.get_node(node_id)
    g = graph.graph

    impl_nodes: list[SymbolNode] = []
    for src in g.predecessors(node_id):
        edges = g[src][node_id]
        if any(e.get("kind") == EdgeKind.IMPLEMENTS.value for e in edges.values()):
            node = graph.get_node(src)
            if node:
                impl_nodes.append(node)

    subject_name = subject.name if subject else node_id
    return StructuralResult(
        query_type="implementors",
        subject=subject,
        nodes=impl_nodes,
        message=f"{len(impl_nodes)} implementor(s) of '{subject_name}'",
    )


def call_chain(
    graph: GraphStore,
    from_id: str,
    to_id: str,
) -> StructuralResult:
    """
    Find the shortest path of CALLS edges from ``from_id`` to ``to_id``.

    Uses BFS over the CALLS sub-graph (ignores IMPORTS, DEFINES, etc.) so
    only actual call relationships are traversed.

    Parameters
    ----------
    graph : GraphStore
    from_id : str
        node_id of the starting symbol.
    to_id : str
        node_id of the target symbol.

    Returns
    -------
    StructuralResult
        ``.path`` contains the ordered list of nodes on the shortest path.
        ``.nodes`` is the same as ``.path`` for convenience.
        If no path exists, ``.path`` is empty and ``.message`` explains why.
    """
    import networkx as nx

    from_node = graph.get_node(from_id)
    to_node = graph.get_node(to_id)

    # Build a CALLS-only view of the graph for the path search.
    g = graph.graph
    calls_view: nx.MultiDiGraph  # type: ignore[type-arg]
    calls_view = nx.MultiDiGraph(
        (src, tgt, attrs)
        for src, tgt, attrs in g.edges(data=True)
        if attrs.get("kind") == EdgeKind.CALLS.value
    )

    try:
        node_ids = nx.shortest_path(calls_view, source=from_id, target=to_id)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        from_name = from_node.name if from_node else from_id
        to_name = to_node.name if to_node else to_id
        return StructuralResult(
            query_type="call_chain",
            subject=from_node,
            path=[],
            nodes=[],
            message=f"No call path found from '{from_name}' to '{to_name}'",
        )

    path_nodes = [n for nid in node_ids if (n := graph.get_node(nid))]
    from_name = from_node.name if from_node else from_id
    to_name = to_node.name if to_node else to_id
    return StructuralResult(
        query_type="call_chain",
        subject=from_node,
        path=path_nodes,
        nodes=path_nodes,
        message=f"Call chain from '{from_name}' to '{to_name}': {len(path_nodes)} step(s)",
    )


def imports_of(graph: GraphStore, file_path: str) -> StructuralResult:
    """
    Find all files/modules imported by the given source file.

    Only follows direct IMPORTS edges (not transitive).

    Parameters
    ----------
    graph : GraphStore
    file_path : str
        Relative path from project root (e.g. "src/auth.py").

    Returns
    -------
    StructuralResult
        ``.nodes`` contains all directly imported targets.
    """
    g = graph.graph
    subject = graph.get_node(file_path)

    imported: list[SymbolNode] = []
    if g.has_node(file_path):
        for tgt in g.successors(file_path):
            edges = g[file_path][tgt]
            if any(e.get("kind") == EdgeKind.IMPORTS.value for e in edges.values()):
                node = graph.get_node(tgt)
                if node:
                    imported.append(node)

    return StructuralResult(
        query_type="imports_of",
        subject=subject,
        nodes=imported,
        message=f"'{file_path}' imports {len(imported)} module(s)",
    )


def find_symbol(graph: GraphStore, name: str) -> StructuralResult:
    """
    Find all SymbolNodes whose short name matches (case-insensitive).

    Useful when the user knows a function name but not its file.

    Parameters
    ----------
    graph : GraphStore
    name : str
        Short symbol name (e.g. "validate_token").

    Returns
    -------
    StructuralResult
        ``.nodes`` contains all matching symbols.
    """
    matches = graph.find_by_name(name)
    return StructuralResult(
        query_type="find_symbol",
        nodes=matches,
        message=f"Found {len(matches)} symbol(s) named '{name}'",
    )
