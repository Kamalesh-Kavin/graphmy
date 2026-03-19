"""
tests/test_nl_query.py
======================
Unit tests for the NLQuery engine (graphmy/query/_nl.py).

Strategy: stub out the heavy dependencies (Embedder and VectorStore) so these
tests run fast — no model download, no chromadb on disk. We verify the
pipeline logic, result construction, and edge-case handling.

All stubs are implemented as minimal subclasses that override the methods
that would otherwise load the model or hit the vector database.
"""

from __future__ import annotations

from typing import Any

import pytest

from graphmy.graph._model import Edge, EdgeKind, SymbolKind, SymbolNode
from graphmy.graph._store import GraphStore
from graphmy.query._nl import NLHit, NLQuery, NLQueryResult
from graphmy.search._embedder import Embedder
from graphmy.search._vector_store import VectorStore

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class StubEmbedder(Embedder):
    """
    Embedder that returns a trivial constant vector.
    Avoids loading sentence-transformers during tests.
    """

    def __init__(self) -> None:
        # Intentionally do NOT call super().__init__() to skip model name setup
        self.model_name = "stub"
        self._model = None  # type: ignore[assignment]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        # Return a 768-dim zero vector for every text — shape matches the real model
        return [[0.0] * 768 for _ in texts]

    def _load(self):  # type: ignore[override]
        raise RuntimeError("StubEmbedder must never actually load a model")


class StubVectorStore(VectorStore):
    """
    VectorStore that returns pre-configured fake search results.
    Avoids chromadb entirely.
    """

    def __init__(self, fake_results: list[dict[str, Any]]) -> None:
        # Do NOT call super().__init__() — that would open a chromadb collection
        self._fake_results = fake_results

    def query(self, text: str, n_results: int = 10) -> list[dict[str, Any]]:
        """Return the pre-configured fake results, capped to n_results."""
        return self._fake_results[:n_results]

    def upsert(self, node_id: str, text: str, metadata: dict[str, Any] | None = None) -> None:
        pass  # no-op for tests


# ---------------------------------------------------------------------------
# Helper: build a small GraphStore with sample nodes
# ---------------------------------------------------------------------------


def _make_store() -> tuple[GraphStore, dict[str, SymbolNode]]:
    """
    Build a tiny GraphStore with three nodes:
      - greet (FUNCTION)
      - fetch_data (FUNCTION, calls greet)
      - Animal (CLASS)

    Returns (store, nodes_by_name).
    """
    store = GraphStore()
    nodes = {}

    for node_id, name, kind in [
        ("app.py::greet", "greet", SymbolKind.FUNCTION),
        ("app.py::fetch_data", "fetch_data", SymbolKind.FUNCTION),
        ("app.py::Animal", "Animal", SymbolKind.CLASS),
    ]:
        n = SymbolNode(
            node_id=node_id,
            kind=kind,
            name=name,
            qualified=name,
            file="app.py",
            line=1,
            end_line=10,
            language="python",
            docstring=f"Docstring for {name}",
            signature=f"def {name}()" if kind == SymbolKind.FUNCTION else f"class {name}:",
        )
        store.add_node(n)
        nodes[name] = n

    # fetch_data calls greet
    store.add_edge(
        Edge(
            source_id="app.py::fetch_data",
            target_id="app.py::greet",
            kind=EdgeKind.CALLS,
        )
    )
    return store, nodes


# ---------------------------------------------------------------------------
# Tests: NLQuery.run() without explain
# ---------------------------------------------------------------------------


class TestNLQueryRun:
    @pytest.fixture()
    def nl_query_setup(self):
        """Return (NLQuery, store, nodes) with stubs in place."""
        store, nodes = _make_store()
        fake_results = [
            {"node_id": "app.py::greet", "distance": 0.1},
            {"node_id": "app.py::fetch_data", "distance": 0.3},
        ]
        vector_store = StubVectorStore(fake_results)
        embedder = StubEmbedder()
        nlq = NLQuery(graph=store, vector_store=vector_store, embedder=embedder)
        return nlq, store, nodes

    def test_returns_nlquery_result(self, nl_query_setup):
        nlq, store, nodes = nl_query_setup
        result = nlq.run("find greeting functions")
        assert isinstance(result, NLQueryResult)

    def test_query_string_preserved(self, nl_query_setup):
        nlq, _, _ = nl_query_setup
        result = nlq.run("find greeting functions")
        assert result.query == "find greeting functions"

    def test_direct_hits_returned(self, nl_query_setup):
        nlq, _, _ = nl_query_setup
        result = nlq.run("find greeting functions")
        hit_names = [h.node.name for h in result.hits]
        assert "greet" in hit_names

    def test_hits_are_sorted_by_distance(self, nl_query_setup):
        nlq, _, _ = nl_query_setup
        result = nlq.run("greeting", limit=5)
        # All direct hits should come before expansion hits
        direct = [h for h in result.hits if not h.is_expansion]
        expansion = [h for h in result.hits if h.is_expansion]
        # Direct hits appear first
        if direct and expansion:
            assert result.hits.index(direct[-1]) < result.hits.index(expansion[0])

    def test_limit_is_respected(self, nl_query_setup):
        nlq, _, _ = nl_query_setup
        result = nlq.run("greeting", limit=1)
        assert len(result.hits) <= 1

    def test_graph_expansion_adds_callers(self, nl_query_setup):
        """greet is called by fetch_data — expansion should surface fetch_data."""
        nlq, _, _ = nl_query_setup
        # Use high limit so expansion is not capped
        result = nlq.run("greeting", limit=20)
        hit_names = {h.node.name for h in result.hits}
        # greet is a direct hit; fetch_data may appear as expansion (it calls greet)
        # fetch_data is ALSO a direct hit in our fake results, so just check both exist
        assert "greet" in hit_names
        assert "fetch_data" in hit_names

    def test_no_duplicate_nodes(self, nl_query_setup):
        """The same node must not appear twice in hits."""
        nlq, _, _ = nl_query_setup
        result = nlq.run("greeting", limit=20)
        ids = [h.node.node_id for h in result.hits]
        assert len(ids) == len(set(ids)), "Duplicate node IDs in hits"

    def test_explanation_empty_without_explain(self, nl_query_setup):
        nlq, _, _ = nl_query_setup
        result = nlq.run("greeting")
        assert result.explanation == ""

    def test_hits_have_callers_and_callees(self, nl_query_setup):
        """Each NLHit should have callers/callees populated from the graph."""
        nlq, _, _ = nl_query_setup
        result = nlq.run("greeting", limit=5)
        greet_hit = next((h for h in result.hits if h.node.name == "greet"), None)
        assert greet_hit is not None
        # fetch_data calls greet, so greet should have fetch_data as a caller
        caller_names = [n.name for n in greet_hit.callers]
        assert "fetch_data" in caller_names


class TestNLQueryExplain:
    def test_explain_without_key_returns_message(self):
        """Without an API key, explain=True must return a helpful message, not raise."""
        store, nodes = _make_store()
        vector_store = StubVectorStore([{"node_id": "app.py::greet", "distance": 0.1}])
        embedder = StubEmbedder()
        nlq = NLQuery(
            graph=store,
            vector_store=vector_store,
            embedder=embedder,
            openai_api_key=None,  # no key
        )
        result = nlq.run("greeting", explain=True)
        assert result.explanation != ""
        # Should tell the user how to configure the key
        assert "key" in result.explanation.lower() or "openai" in result.explanation.lower()


class TestNLHitSerialisation:
    def test_as_dict_is_json_friendly(self):
        store, nodes = _make_store()
        hit = NLHit(
            node=nodes["greet"],
            distance=0.1,
            is_expansion=False,
            callers=[],
            callees=[],
        )
        d = hit.as_dict()
        assert d["distance"] == 0.1
        assert d["is_expansion"] is False
        assert "node" in d
        assert isinstance(d["callers"], list)


class TestNLQueryResultSerialisation:
    def test_as_dict_structure(self):
        result = NLQueryResult(query="test query", hits=[], explanation="")
        d = result.as_dict()
        assert d["query"] == "test query"
        assert d["hits"] == []
        assert d["explanation"] == ""


class TestUnknownNodeIdGraceful:
    def test_missing_node_id_in_graph_is_skipped(self):
        """If vector store returns a node_id not in graph, it is silently skipped."""
        store = GraphStore()
        vector_store = StubVectorStore([{"node_id": "nonexistent::node", "distance": 0.05}])
        embedder = StubEmbedder()
        nlq = NLQuery(graph=store, vector_store=vector_store, embedder=embedder)
        result = nlq.run("anything")
        # No crash, just no hits
        assert result.hits == []
