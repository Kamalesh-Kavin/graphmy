"""
tests/test_indexer_go.py
========================
Unit tests for the Go language parser (GoParser).

We parse tests/fixtures/sample_go/main.go and assert that:
  - Top-level functions are extracted as FUNCTION kind
  - Structs are extracted as STRUCT kind
  - Interfaces are extracted as INTERFACE kind
  - Methods on struct types are extracted as METHOD kind
  - CONTAINS edges link receiver types to their methods
  - IMPORTS edges are recorded for Go import paths
  - Go doc comments are extracted as docstrings
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphmy.graph._model import EdgeKind, SymbolKind
from graphmy.indexer._go import GoParser


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_go"
FIXTURE_FILE = FIXTURE_DIR / "main.go"


@pytest.fixture(scope="module")
def parse_result():
    """Parse the Go fixture once per session."""
    parser = GoParser()
    source = FIXTURE_FILE.read_text(encoding="utf-8")
    return parser.parse(FIXTURE_FILE, source, FIXTURE_DIR)


@pytest.fixture(scope="module")
def nodes_by_name(parse_result):
    result: dict[str, list] = {}
    for node in parse_result.nodes:
        result.setdefault(node.name, []).append(node)
    return result


@pytest.fixture(scope="module")
def edges_by_kind(parse_result):
    result: dict[EdgeKind, list] = {}
    for edge in parse_result.edges:
        result.setdefault(edge.kind, []).append(edge)
    return result


# ---------------------------------------------------------------------------
# Type declarations
# ---------------------------------------------------------------------------


class TestStructs:
    def test_dog_struct_extracted(self, nodes_by_name):
        assert "Dog" in nodes_by_name, "Dog struct should be extracted"
        node = nodes_by_name["Dog"][0]
        assert node.kind == SymbolKind.STRUCT

    def test_struct_language(self, nodes_by_name):
        node = nodes_by_name["Dog"][0]
        assert node.language == "go"

    def test_struct_file_relative(self, nodes_by_name):
        node = nodes_by_name["Dog"][0]
        assert not Path(node.file).is_absolute()
        assert node.file.endswith("main.go")


class TestInterfaces:
    def test_animal_interface_extracted(self, nodes_by_name):
        assert "Animal" in nodes_by_name, "Animal interface should be extracted"
        node = nodes_by_name["Animal"][0]
        assert node.kind == SymbolKind.INTERFACE


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


class TestFunctions:
    def test_greet_extracted(self, nodes_by_name):
        assert "Greet" in nodes_by_name
        node = nodes_by_name["Greet"][0]
        assert node.kind == SymbolKind.FUNCTION

    def test_new_dog_extracted(self, nodes_by_name):
        assert "NewDog" in nodes_by_name
        node = nodes_by_name["NewDog"][0]
        assert node.kind == SymbolKind.FUNCTION

    def test_process_names_extracted(self, nodes_by_name):
        assert "processNames" in nodes_by_name
        node = nodes_by_name["processNames"][0]
        assert node.kind == SymbolKind.FUNCTION

    def test_main_extracted(self, nodes_by_name):
        assert "main" in nodes_by_name

    def test_greet_has_docstring(self, nodes_by_name):
        """Go doc comment '// Greet returns...' must be captured as docstring."""
        node = nodes_by_name["Greet"][0]
        assert node.docstring, "Greet should have a docstring from its // comment"
        assert "greeting" in node.docstring.lower()


# ---------------------------------------------------------------------------
# Methods on receiver types
# ---------------------------------------------------------------------------


class TestMethods:
    def test_speak_method_extracted(self, nodes_by_name):
        assert "Speak" in nodes_by_name
        node = nodes_by_name["Speak"][0]
        assert node.kind == SymbolKind.METHOD

    def test_describe_method_extracted(self, nodes_by_name):
        assert "Describe" in nodes_by_name
        node = nodes_by_name["Describe"][0]
        assert node.kind == SymbolKind.METHOD

    def test_method_language(self, nodes_by_name):
        node = nodes_by_name["Speak"][0]
        assert node.language == "go"

    def test_method_node_id_includes_receiver(self, nodes_by_name):
        """Method node_id must be '<file>::Dog::Speak' (receiver type included)."""
        node = nodes_by_name["Speak"][0]
        assert "Dog" in node.node_id, f"Expected 'Dog' in node_id, got {node.node_id!r}"

    def test_speak_has_docstring(self, nodes_by_name):
        node = nodes_by_name["Speak"][0]
        assert node.docstring, "Speak method should have its doc comment"


# ---------------------------------------------------------------------------
# CONTAINS edges: Dog → Speak, Dog → Describe
# ---------------------------------------------------------------------------


class TestContainsEdges:
    def test_dog_contains_speak(self, parse_result, nodes_by_name):
        contains = [e for e in parse_result.edges if e.kind == EdgeKind.CONTAINS]
        assert contains, "Expected CONTAINS edges"
        speak_node = nodes_by_name["Speak"][0]
        assert any(e.target_id == speak_node.node_id for e in contains), (
            "Expected a CONTAINS edge pointing to Speak"
        )

    def test_dog_contains_describe(self, parse_result, nodes_by_name):
        contains = [e for e in parse_result.edges if e.kind == EdgeKind.CONTAINS]
        describe_node = nodes_by_name["Describe"][0]
        assert any(e.target_id == describe_node.node_id for e in contains)


# ---------------------------------------------------------------------------
# IMPORTS edges
# ---------------------------------------------------------------------------


class TestImports:
    def test_fmt_import(self, parse_result):
        imports = [e for e in parse_result.edges if e.kind == EdgeKind.IMPORTS]
        assert imports, "Expected IMPORTS edges from Go import block"
        assert any("fmt" in e.target_id for e in imports), "Expected import of 'fmt'"

    def test_strings_import(self, parse_result):
        imports = [e for e in parse_result.edges if e.kind == EdgeKind.IMPORTS]
        assert any("strings" in e.target_id for e in imports), "Expected import of 'strings'"
