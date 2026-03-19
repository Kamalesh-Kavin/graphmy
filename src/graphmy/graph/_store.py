"""
graphmy/graph/_store.py
=======================
NetworkX DiGraph wrapper — the heart of the graphmy knowledge graph.

GraphStore owns the in-memory networkx DiGraph and handles:
  - Adding/updating nodes (SymbolNode) and edges (Edge)
  - Removing all symbols from a given file (for incremental re-index)
  - Persisting the full graph to .graphmy/graph.json
  - Loading the graph back from JSON
  - Convenience query methods (callers, callees, neighbours, stats)

Why networkx DiGraph?
  A directed graph is the natural model for code relationships:
    - CALLS edges go from caller → callee
    - IMPORTS edges go from importer → imported
    - INHERITS edges go from subclass → superclass
  This lets us answer "who calls X?" with G.predecessors(X) and
  "what does X call?" with G.successors(X) — both O(degree) operations.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import networkx as nx

from graphmy.graph._model import Edge, EdgeKind, SymbolKind, SymbolNode


class GraphStore:
    """
    The graphmy knowledge graph.

    Internally uses a networkx MultiDiGraph so that two nodes can have more
    than one edge between them (e.g. a file that both imports AND calls a
    symbol from another file).

    Parameters
    ----------
    project_root : Path
        Used only for relative-path display in stats output. Not stored.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        # MultiDiGraph: directed, allows parallel edges (different edge kinds
        # between the same pair of nodes).
        self._g: nx.MultiDiGraph  # type: ignore[type-arg]
        self._g = nx.MultiDiGraph()

        # Separate mapping from file path → set of node_ids defined in that file.
        # Maintained in sync with the graph so we can efficiently remove all
        # symbols from a file during incremental re-index without a full scan.
        self._file_to_nodes: dict[str, set[str]] = {}

        self.project_root = project_root

    # ------------------------------------------------------------------
    # Adding nodes and edges
    # ------------------------------------------------------------------

    def add_node(self, node: SymbolNode) -> None:
        """
        Add or update a SymbolNode in the graph.

        If a node with the same node_id already exists, its attributes are
        replaced (useful for incremental updates).

        All SymbolNode fields are stored as flat networkx node attributes
        (via to_dict()) so they survive JSON round-trips.
        """
        self._g.add_node(node.node_id, **node.to_dict())

        # Track which file this node belongs to (skip FILE and EXTERNAL nodes
        # since they are not children of a file).
        if node.file and node.kind not in (SymbolKind.FILE, SymbolKind.EXTERNAL):
            self._file_to_nodes.setdefault(node.file, set()).add(node.node_id)

    def add_edge(self, edge: Edge) -> None:
        """
        Add a directed edge between two nodes.

        If either endpoint doesn't exist yet in the graph, networkx silently
        creates a node with no attributes. This is intentional — a CALLS edge
        to an as-yet-unindexed symbol is valid during incremental indexing.
        Missing node attributes will be filled in when that file is indexed.
        """
        self._g.add_edge(
            edge.source_id,
            edge.target_id,
            kind=edge.kind.value,
        )

    # ------------------------------------------------------------------
    # Removing a file's symbols (incremental re-index)
    # ------------------------------------------------------------------

    def remove_file(self, file_path: str) -> None:
        """
        Remove all nodes and edges associated with a source file.

        Called by the incremental indexer before re-parsing a changed file.
        After removal the file can be re-parsed and its new symbols added
        cleanly without duplicates.

        Parameters
        ----------
        file_path : str
            Relative path (from project root) as stored in SymbolNode.file.
        """
        node_ids = self._file_to_nodes.pop(file_path, set())

        # Also remove the FILE node itself (node_id == the file path string).
        node_ids.add(file_path)

        for nid in node_ids:
            if self._g.has_node(nid):
                self._g.remove_node(nid)
                # networkx removes all edges incident to this node automatically.

    # ------------------------------------------------------------------
    # Node access
    # ------------------------------------------------------------------

    def get_node(self, node_id: str) -> SymbolNode | None:
        """
        Look up a SymbolNode by its node_id. Returns None if not found.
        """
        if not self._g.has_node(node_id):
            return None
        return SymbolNode.from_dict(dict(self._g.nodes[node_id]))

    def all_nodes(self) -> Iterator[SymbolNode]:
        """Iterate over all SymbolNodes in the graph."""
        for _, attrs in self._g.nodes(data=True):
            if attrs:  # skip ghost nodes with no attributes
                yield SymbolNode.from_dict(dict(attrs))

    def nodes_for_file(self, file_path: str) -> list[SymbolNode]:
        """All non-FILE SymbolNodes defined in a given source file."""
        result = []
        for nid in self._file_to_nodes.get(file_path, set()):
            node = self.get_node(nid)
            if node:
                result.append(node)
        return result

    # ------------------------------------------------------------------
    # Structural query helpers (used by query/_structural.py)
    # ------------------------------------------------------------------

    def callers(self, node_id: str) -> list[SymbolNode]:
        """
        Return all nodes that have a CALLS edge pointing TO node_id.
        i.e. "who calls this function?"
        """
        result = []
        for src in self._g.predecessors(node_id):
            # Check that at least one edge between src → node_id is a CALLS edge.
            edges = self._g[src][node_id]
            if any(e.get("kind") == EdgeKind.CALLS.value for e in edges.values()):
                node = self.get_node(src)
                if node:
                    result.append(node)
        return result

    def callees(self, node_id: str) -> list[SymbolNode]:
        """
        Return all nodes that node_id has a CALLS edge pointing TO.
        i.e. "what does this function call?"
        """
        result = []
        for tgt in self._g.successors(node_id):
            edges = self._g[node_id][tgt]
            if any(e.get("kind") == EdgeKind.CALLS.value for e in edges.values()):
                node = self.get_node(tgt)
                if node:
                    result.append(node)
        return result

    def subclasses(self, node_id: str) -> list[SymbolNode]:
        """All classes that INHERIT from node_id."""
        result = []
        for src in self._g.predecessors(node_id):
            edges = self._g[src][node_id]
            if any(e.get("kind") == EdgeKind.INHERITS.value for e in edges.values()):
                node = self.get_node(src)
                if node:
                    result.append(node)
        return result

    def superclasses(self, node_id: str) -> list[SymbolNode]:
        """All classes that node_id INHERITS from."""
        result = []
        for tgt in self._g.successors(node_id):
            edges = self._g[node_id][tgt]
            if any(e.get("kind") == EdgeKind.INHERITS.value for e in edges.values()):
                node = self.get_node(tgt)
                if node:
                    result.append(node)
        return result

    def find_by_name(self, name: str) -> list[SymbolNode]:
        """
        Return all SymbolNodes whose short name matches (case-insensitive).
        Used as a fallback when the exact node_id is not known.
        """
        name_lower = name.lower()
        return [node for node in self.all_nodes() if node.name.lower() == name_lower]

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """
        Return a summary dict with counts of nodes by kind, edge types, and
        language breakdown. Used by `graphmy info`.
        """
        kind_counts: dict[str, int] = {}
        edge_counts: dict[str, int] = {}
        lang_counts: dict[str, int] = {}

        for _, attrs in self._g.nodes(data=True):
            if not attrs:
                continue
            kind_counts[attrs.get("kind", "?")] = kind_counts.get(attrs.get("kind", "?"), 0) + 1
            lang = attrs.get("language", "")
            if lang:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1

        for _, _, attrs in self._g.edges(data=True):
            k = attrs.get("kind", "?")
            edge_counts[k] = edge_counts.get(k, 0) + 1

        return {
            "total_nodes": self._g.number_of_nodes(),
            "total_edges": self._g.number_of_edges(),
            "by_kind": kind_counts,
            "by_edge": edge_counts,
            "by_language": lang_counts,
        }

    # ------------------------------------------------------------------
    # Persistence — save/load JSON
    # ------------------------------------------------------------------

    def save(self, path: Path) -> None:
        """
        Serialise the full graph to a JSON file using networkx node-link format.

        Node-link format stores the graph as:
          {
            "directed": true,
            "multigraph": true,
            "nodes": [{id, ...attrs}, ...],
            "links": [{source, target, kind, ...}, ...]
          }

        This is the standard networkx interchange format, readable by
        networkx.node_link_graph() and by tools like d3.js.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        data = nx.node_link_data(self._g, edges="links")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def load(cls, path: Path, project_root: Path | None = None) -> GraphStore:
        """
        Load a GraphStore from a JSON file previously saved by save().

        Also reconstructs the _file_to_nodes mapping by scanning node attributes,
        so incremental indexing works correctly after loading from disk.
        """
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        store = cls(project_root=project_root)
        store._g = nx.node_link_graph(data, directed=True, multigraph=True, edges="links")

        # Rebuild the file → nodes mapping from node attributes.
        for nid, attrs in store._g.nodes(data=True):
            if not attrs:
                continue
            file_path = attrs.get("file", "")
            kind = attrs.get("kind", "")
            if file_path and kind not in (SymbolKind.FILE.value, SymbolKind.EXTERNAL.value):
                store._file_to_nodes.setdefault(file_path, set()).add(nid)

        return store

    # ------------------------------------------------------------------
    # Internal access (for viz exporter)
    # ------------------------------------------------------------------

    @property
    def graph(self) -> nx.MultiDiGraph:  # type: ignore[type-arg]
        """Direct access to the underlying networkx graph. Use with care."""
        return self._g
