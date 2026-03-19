"""
tests/test_structural_query.py
==============================
Unit tests for graphmy/query/_structural.py.

We build small hand-crafted GraphStore instances (no file I/O, no tree-sitter)
and verify that each structural query function returns the correct result.

Tests cover:
  - callers()         — who calls a given symbol?
  - callees()         — what does a symbol call?
  - subclasses()      — classes inheriting from a symbol
  - superclasses()    — ancestors of a symbol
  - implementors()    — classes implementing an interface
  - call_chain()      — shortest CALLS path between two nodes
  - imports_of()      — direct imports of a file
  - find_symbol()     — case-insensitive name search
"""

from __future__ import annotations

import pytest

from graphmy.graph._model import Edge, EdgeKind, SymbolKind, SymbolNode
from graphmy.graph._store import GraphStore
from graphmy.query._structural import (
    call_chain,
    callees,
    callers,
    find_symbol,
    implementors,
    imports_of,
    subclasses,
    superclasses,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fn(node_id: str, name: str, file: str = "app.py") -> SymbolNode:
    return SymbolNode(
        node_id=node_id,
        kind=SymbolKind.FUNCTION,
        name=name,
        qualified=name,
        file=file,
        line=1,
        end_line=10,
        language="python",
    )


def cls(node_id: str, name: str, file: str = "app.py") -> SymbolNode:
    return SymbolNode(
        node_id=node_id,
        kind=SymbolKind.CLASS,
        name=name,
        qualified=name,
        file=file,
        line=1,
        end_line=20,
        language="python",
    )


def iface(node_id: str, name: str, file: str = "app.py") -> SymbolNode:
    return SymbolNode(
        node_id=node_id,
        kind=SymbolKind.INTERFACE,
        name=name,
        qualified=name,
        file=file,
        line=1,
        end_line=10,
        language="typescript",
    )


def edge(src: str, tgt: str, kind: EdgeKind) -> Edge:
    return Edge(source_id=src, target_id=tgt, kind=kind)


# ---------------------------------------------------------------------------
# callers()
# ---------------------------------------------------------------------------


class TestCallers:
    def test_returns_direct_callers(self):
        store = GraphStore()
        store.add_node(fn("f::a", "a"))
        store.add_node(fn("f::b", "b"))
        store.add_edge(edge("f::a", "f::b", EdgeKind.CALLS))

        result = callers(store, "f::b")
        assert result.query_type == "callers"
        assert len(result.nodes) == 1
        assert result.nodes[0].name == "a"

    def test_no_callers_returns_empty(self):
        store = GraphStore()
        store.add_node(fn("f::a", "a"))
        result = callers(store, "f::a")
        assert result.nodes == []

    def test_multiple_callers(self):
        store = GraphStore()
        for name in ("target", "c1", "c2", "c3"):
            store.add_node(fn(f"f::{name}", name))
        store.add_edge(edge("f::c1", "f::target", EdgeKind.CALLS))
        store.add_edge(edge("f::c2", "f::target", EdgeKind.CALLS))
        store.add_edge(edge("f::c3", "f::target", EdgeKind.CALLS))

        result = callers(store, "f::target")
        assert len(result.nodes) == 3

    def test_message_contains_count(self):
        store = GraphStore()
        store.add_node(fn("f::x", "x"))
        result = callers(store, "f::x")
        assert "0" in result.message

    def test_subject_is_queried_node(self):
        store = GraphStore()
        store.add_node(fn("f::foo", "foo"))
        result = callers(store, "f::foo")
        assert result.subject is not None
        assert result.subject.name == "foo"


# ---------------------------------------------------------------------------
# callees()
# ---------------------------------------------------------------------------


class TestCallees:
    def test_returns_direct_callees(self):
        store = GraphStore()
        store.add_node(fn("f::a", "a"))
        store.add_node(fn("f::b", "b"))
        store.add_edge(edge("f::a", "f::b", EdgeKind.CALLS))

        result = callees(store, "f::a")
        assert len(result.nodes) == 1
        assert result.nodes[0].name == "b"

    def test_ignores_non_calls_edges(self):
        """IMPORTS edges must not appear in callees result."""
        store = GraphStore()
        store.add_node(fn("f::a", "a"))
        store.add_node(fn("f::b", "b"))
        store.add_edge(edge("f::a", "f::b", EdgeKind.IMPORTS))

        result = callees(store, "f::a")
        assert result.nodes == []


# ---------------------------------------------------------------------------
# subclasses() and superclasses()
# ---------------------------------------------------------------------------


class TestInheritanceQueries:
    @pytest.fixture()
    def hierarchy(self):
        """Animal ← Dog ← GoldenRetriever"""
        store = GraphStore()
        animal = cls("f::Animal", "Animal")
        dog = cls("f::Dog", "Dog")
        golden = cls("f::GoldenRetriever", "GoldenRetriever")
        store.add_node(animal)
        store.add_node(dog)
        store.add_node(golden)
        store.add_edge(edge("f::Dog", "f::Animal", EdgeKind.INHERITS))
        store.add_edge(edge("f::GoldenRetriever", "f::Dog", EdgeKind.INHERITS))
        return store

    def test_subclasses_of_animal(self, hierarchy):
        result = subclasses(hierarchy, "f::Animal")
        names = {n.name for n in result.nodes}
        assert "Dog" in names

    def test_subclasses_does_not_include_grandchild(self, hierarchy):
        """subclasses() is direct only (depth=1)."""
        result = subclasses(hierarchy, "f::Animal")
        names = {n.name for n in result.nodes}
        assert "GoldenRetriever" not in names

    def test_superclasses_of_dog(self, hierarchy):
        result = superclasses(hierarchy, "f::Dog")
        names = {n.name for n in result.nodes}
        assert "Animal" in names

    def test_superclasses_of_animal_is_empty(self, hierarchy):
        result = superclasses(hierarchy, "f::Animal")
        assert result.nodes == []


# ---------------------------------------------------------------------------
# implementors()
# ---------------------------------------------------------------------------


class TestImplementors:
    def test_finds_implementing_class(self):
        store = GraphStore()
        runnable = iface("f::Runnable", "Runnable")
        dog = cls("f::Dog", "Dog")
        store.add_node(runnable)
        store.add_node(dog)
        store.add_edge(edge("f::Dog", "f::Runnable", EdgeKind.IMPLEMENTS))

        result = implementors(store, "f::Runnable")
        assert len(result.nodes) == 1
        assert result.nodes[0].name == "Dog"

    def test_no_implementors_returns_empty(self):
        store = GraphStore()
        store.add_node(iface("f::Empty", "Empty"))
        result = implementors(store, "f::Empty")
        assert result.nodes == []


# ---------------------------------------------------------------------------
# call_chain()
# ---------------------------------------------------------------------------


class TestCallChain:
    @pytest.fixture()
    def chain_graph(self):
        """a → b → c (CALLS chain)"""
        store = GraphStore()
        for name in ("a", "b", "c"):
            store.add_node(fn(f"f::{name}", name))
        store.add_edge(edge("f::a", "f::b", EdgeKind.CALLS))
        store.add_edge(edge("f::b", "f::c", EdgeKind.CALLS))
        return store

    def test_finds_direct_chain(self, chain_graph):
        result = call_chain(chain_graph, "f::a", "f::c")
        assert len(result.path) == 3
        names = [n.name for n in result.path]
        assert names == ["a", "b", "c"]

    def test_no_path_returns_empty(self, chain_graph):
        result = call_chain(chain_graph, "f::c", "f::a")
        assert result.path == []
        assert "No call path" in result.message

    def test_same_start_and_end(self, chain_graph):
        """call_chain from a node to itself returns just that node."""
        result = call_chain(chain_graph, "f::a", "f::a")
        assert len(result.path) >= 1


# ---------------------------------------------------------------------------
# imports_of()
# ---------------------------------------------------------------------------


class TestImportsOf:
    def test_finds_imported_modules(self):
        store = GraphStore()
        # Add a file node (kind=FILE) and an external stub
        file_node = SymbolNode(
            node_id="src/app.py",
            kind=SymbolKind.FILE,
            name="app.py",
            qualified="src/app.py",
            file="src/app.py",
            line=0,
            end_line=0,
            language="python",
        )
        ext_node = SymbolNode(
            node_id="ext::os",
            kind=SymbolKind.EXTERNAL,
            name="os",
            qualified="os",
            file="",
            line=0,
            end_line=0,
            language="",
        )
        store.add_node(file_node)
        store.add_node(ext_node)
        store.add_edge(edge("src/app.py", "ext::os", EdgeKind.IMPORTS))

        result = imports_of(store, "src/app.py")
        assert len(result.nodes) == 1
        assert result.nodes[0].name == "os"


# ---------------------------------------------------------------------------
# find_symbol()
# ---------------------------------------------------------------------------


class TestFindSymbol:
    def test_finds_by_exact_name(self):
        store = GraphStore()
        store.add_node(fn("f::greet", "greet"))
        result = find_symbol(store, "greet")
        assert len(result.nodes) == 1

    def test_finds_case_insensitive(self):
        store = GraphStore()
        store.add_node(fn("f::Greet", "Greet"))
        result = find_symbol(store, "greet")
        assert len(result.nodes) == 1

    def test_returns_empty_for_unknown_name(self):
        store = GraphStore()
        result = find_symbol(store, "totally_unknown_xyx")
        assert result.nodes == []

    def test_finds_multiple_with_same_name(self):
        """Two files can have a function named 'greet' — both are returned."""
        store = GraphStore()
        store.add_node(fn("a.py::greet", "greet", file="a.py"))
        store.add_node(fn("b.py::greet", "greet", file="b.py"))
        result = find_symbol(store, "greet")
        assert len(result.nodes) == 2

    def test_as_dict_is_serialisable(self):
        store = GraphStore()
        store.add_node(fn("f::foo", "foo"))
        result = find_symbol(store, "foo")
        d = result.as_dict()
        assert d["query_type"] == "find_symbol"
        assert isinstance(d["nodes"], list)
