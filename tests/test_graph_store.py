"""
tests/test_graph_store.py
=========================
Unit tests for GraphStore — the NetworkX-backed in-memory knowledge graph.

Tests cover:
  - Adding and retrieving nodes
  - Adding edges and querying callers/callees/subclasses
  - Removing all symbols from a file (incremental re-index support)
  - JSON round-trip: save() then load() preserves all nodes and edges
  - stats() returns correct counts
  - find_by_name() is case-insensitive
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graphmy.graph._model import Edge, EdgeKind, SymbolKind, SymbolNode
from graphmy.graph._store import GraphStore


# ---------------------------------------------------------------------------
# Helpers — quick node / edge builders
# ---------------------------------------------------------------------------


def make_node(
    node_id: str,
    name: str,
    kind: SymbolKind = SymbolKind.FUNCTION,
    file: str = "src/app.py",
    line: int = 1,
) -> SymbolNode:
    """Create a minimal SymbolNode for use in tests."""
    return SymbolNode(
        node_id=node_id,
        kind=kind,
        name=name,
        qualified=f"app.{name}",
        file=file,
        line=line,
        end_line=line + 5,
        language="python",
    )


def make_edge(source_id: str, target_id: str, kind: EdgeKind = EdgeKind.CALLS) -> Edge:
    return Edge(source_id=source_id, target_id=target_id, kind=kind)


# ---------------------------------------------------------------------------
# Node operations
# ---------------------------------------------------------------------------


class TestAddAndGetNode:
    def test_add_and_retrieve_node(self, empty_graph):
        node = make_node("src/app.py::greet", "greet")
        empty_graph.add_node(node)
        result = empty_graph.get_node("src/app.py::greet")
        assert result is not None
        assert result.name == "greet"
        assert result.kind == SymbolKind.FUNCTION

    def test_get_nonexistent_node_returns_none(self, empty_graph):
        assert empty_graph.get_node("does::not::exist") is None

    def test_add_node_updates_existing(self, empty_graph):
        """Re-adding a node with the same ID replaces its attributes."""
        node1 = make_node("src/app.py::foo", "foo", line=10)
        node2 = make_node("src/app.py::foo", "foo", line=99)
        empty_graph.add_node(node1)
        empty_graph.add_node(node2)
        result = empty_graph.get_node("src/app.py::foo")
        assert result is not None
        assert result.line == 99

    def test_all_nodes_iterates_all(self):
        store = GraphStore()
        store.add_node(make_node("f::a", "a"))
        store.add_node(make_node("f::b", "b"))
        names = {n.name for n in store.all_nodes()}
        assert "a" in names
        assert "b" in names

    def test_nodes_for_file(self):
        store = GraphStore()
        store.add_node(make_node("src/a.py::foo", "foo", file="src/a.py"))
        store.add_node(make_node("src/a.py::bar", "bar", file="src/a.py"))
        store.add_node(make_node("src/b.py::baz", "baz", file="src/b.py"))
        result = store.nodes_for_file("src/a.py")
        names = {n.name for n in result}
        assert names == {"foo", "bar"}


# ---------------------------------------------------------------------------
# Edge operations and structural queries
# ---------------------------------------------------------------------------


class TestEdgesAndQueries:
    def test_callers(self):
        store = GraphStore()
        a = make_node("f::a", "a")
        b = make_node("f::b", "b")
        store.add_node(a)
        store.add_node(b)
        store.add_edge(make_edge("f::a", "f::b", EdgeKind.CALLS))

        callers = store.callers("f::b")
        assert len(callers) == 1
        assert callers[0].name == "a"

    def test_callees(self):
        store = GraphStore()
        a = make_node("f::a", "a")
        b = make_node("f::b", "b")
        store.add_node(a)
        store.add_node(b)
        store.add_edge(make_edge("f::a", "f::b", EdgeKind.CALLS))

        callees = store.callees("f::a")
        assert len(callees) == 1
        assert callees[0].name == "b"

    def test_no_callers_returns_empty(self, empty_graph):
        empty_graph.add_node(make_node("f::isolated", "isolated"))
        assert empty_graph.callers("f::isolated") == []

    def test_subclasses(self):
        store = GraphStore()
        parent = make_node("f::Animal", "Animal", kind=SymbolKind.CLASS)
        child = make_node("f::Dog", "Dog", kind=SymbolKind.CLASS)
        store.add_node(parent)
        store.add_node(child)
        store.add_edge(make_edge("f::Dog", "f::Animal", EdgeKind.INHERITS))

        subs = store.subclasses("f::Animal")
        assert len(subs) == 1
        assert subs[0].name == "Dog"

    def test_superclasses(self):
        store = GraphStore()
        parent = make_node("f::Animal", "Animal", kind=SymbolKind.CLASS)
        child = make_node("f::Dog", "Dog", kind=SymbolKind.CLASS)
        store.add_node(parent)
        store.add_node(child)
        store.add_edge(make_edge("f::Dog", "f::Animal", EdgeKind.INHERITS))

        supers = store.superclasses("f::Dog")
        assert len(supers) == 1
        assert supers[0].name == "Animal"

    def test_multiple_edge_kinds_between_same_nodes(self):
        """MultiDiGraph must support both CALLS and IMPORTS between the same pair."""
        store = GraphStore()
        store.add_node(make_node("f::a", "a"))
        store.add_node(make_node("f::b", "b"))
        store.add_edge(make_edge("f::a", "f::b", EdgeKind.CALLS))
        store.add_edge(make_edge("f::a", "f::b", EdgeKind.IMPORTS))

        # Both edges exist; callers and callees reflect only CALLS edges
        callees = store.callees("f::a")
        assert len(callees) == 1  # CALLS to b

    def test_find_by_name_case_insensitive(self):
        store = GraphStore()
        store.add_node(make_node("f::Greet", "Greet"))
        results = store.find_by_name("greet")
        assert len(results) == 1
        assert results[0].name == "Greet"


# ---------------------------------------------------------------------------
# remove_file (incremental re-index)
# ---------------------------------------------------------------------------


class TestRemoveFile:
    def test_remove_file_deletes_nodes(self):
        store = GraphStore()
        store.add_node(make_node("src/a.py::foo", "foo", file="src/a.py"))
        store.add_node(make_node("src/a.py::bar", "bar", file="src/a.py"))
        store.remove_file("src/a.py")
        assert store.get_node("src/a.py::foo") is None
        assert store.get_node("src/a.py::bar") is None

    def test_remove_file_does_not_affect_other_files(self):
        store = GraphStore()
        store.add_node(make_node("src/a.py::foo", "foo", file="src/a.py"))
        store.add_node(make_node("src/b.py::bar", "bar", file="src/b.py"))
        store.remove_file("src/a.py")
        assert store.get_node("src/b.py::bar") is not None

    def test_remove_nonexistent_file_is_noop(self):
        store = GraphStore()
        # Should not raise
        store.remove_file("nonexistent.py")


# ---------------------------------------------------------------------------
# JSON persistence: save() / load()
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_save_creates_file(self, tmp_path):
        store = GraphStore()
        store.add_node(make_node("f::a", "a"))
        out = tmp_path / "graph.json"
        store.save(out)
        assert out.exists()

    def test_save_is_valid_json(self, tmp_path):
        store = GraphStore()
        store.add_node(make_node("f::a", "a"))
        out = tmp_path / "graph.json"
        store.save(out)
        data = json.loads(out.read_text())
        assert "nodes" in data
        assert "links" in data

    def test_round_trip_nodes(self, tmp_path):
        """save() then load() must recover the same nodes."""
        store = GraphStore()
        node = make_node("src/app.py::greet", "greet", kind=SymbolKind.FUNCTION, line=24)
        store.add_node(node)
        out = tmp_path / "graph.json"
        store.save(out)

        loaded = GraphStore.load(out)
        result = loaded.get_node("src/app.py::greet")
        assert result is not None
        assert result.name == "greet"
        assert result.kind == SymbolKind.FUNCTION
        assert result.line == 24

    def test_round_trip_edges(self, tmp_path):
        """Edges must survive the JSON round-trip."""
        store = GraphStore()
        store.add_node(make_node("f::a", "a"))
        store.add_node(make_node("f::b", "b"))
        store.add_edge(make_edge("f::a", "f::b", EdgeKind.CALLS))
        out = tmp_path / "graph.json"
        store.save(out)

        loaded = GraphStore.load(out)
        callees = loaded.callees("f::a")
        assert len(callees) == 1
        assert callees[0].name == "b"

    def test_load_rebuilds_file_to_nodes_map(self, tmp_path):
        """After loading, nodes_for_file() must work correctly."""
        store = GraphStore()
        store.add_node(make_node("src/a.py::foo", "foo", file="src/a.py"))
        out = tmp_path / "graph.json"
        store.save(out)

        loaded = GraphStore.load(out)
        nodes = loaded.nodes_for_file("src/a.py")
        assert any(n.name == "foo" for n in nodes)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats_total_counts(self):
        store = GraphStore()
        store.add_node(make_node("f::a", "a", kind=SymbolKind.FUNCTION))
        store.add_node(make_node("f::b", "b", kind=SymbolKind.CLASS))
        store.add_edge(make_edge("f::a", "f::b", EdgeKind.CALLS))
        s = store.stats()
        assert s["total_nodes"] == 2
        assert s["total_edges"] == 1

    def test_stats_by_kind(self):
        store = GraphStore()
        store.add_node(make_node("f::a", "a", kind=SymbolKind.FUNCTION))
        store.add_node(make_node("f::b", "b", kind=SymbolKind.CLASS))
        s = store.stats()
        assert s["by_kind"].get("function") == 1
        assert s["by_kind"].get("class") == 1

    def test_stats_by_edge(self):
        store = GraphStore()
        store.add_node(make_node("f::a", "a"))
        store.add_node(make_node("f::b", "b"))
        store.add_edge(make_edge("f::a", "f::b", EdgeKind.CALLS))
        s = store.stats()
        assert s["by_edge"].get("CALLS") == 1
