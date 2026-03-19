"""
tests/test_indexer_js.py
========================
Unit tests for the JavaScript / TypeScript parser (JavaScriptParser).

We parse tests/fixtures/sample_js/app.js and assert that:
  - Top-level functions are extracted as FUNCTION kind
  - Classes are extracted as CLASS kind
  - Methods inside classes are extracted as METHOD kind
  - Dog extends Animal → INHERITS edge recorded
  - CONTAINS edges link classes to their methods
  - ESM import statement produces an IMPORTS edge
  - language field is set to 'javascript'
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphmy.graph._model import EdgeKind, SymbolKind
from graphmy.indexer._javascript import JavaScriptParser


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_js"
FIXTURE_FILE = FIXTURE_DIR / "app.js"


@pytest.fixture(scope="module")
def parse_result():
    """Parse the JS fixture once per session."""
    parser = JavaScriptParser()
    source = FIXTURE_FILE.read_text(encoding="utf-8")
    return parser.parse(FIXTURE_FILE, source, FIXTURE_DIR)


@pytest.fixture(scope="module")
def nodes_by_name(parse_result):
    result: dict[str, list] = {}
    for node in parse_result.nodes:
        result.setdefault(node.name, []).append(node)
    return result


# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------


class TestFunctions:
    def test_greet_extracted(self, nodes_by_name):
        assert "greet" in nodes_by_name
        node = nodes_by_name["greet"][0]
        assert node.kind == SymbolKind.FUNCTION

    def test_fetch_data_extracted(self, nodes_by_name):
        assert "fetchData" in nodes_by_name
        node = nodes_by_name["fetchData"][0]
        assert node.kind == SymbolKind.FUNCTION

    def test_language_is_javascript(self, nodes_by_name):
        node = nodes_by_name["greet"][0]
        assert node.language == "javascript"

    def test_file_is_relative(self, nodes_by_name):
        node = nodes_by_name["greet"][0]
        assert not Path(node.file).is_absolute()
        assert node.file.endswith("app.js")

    def test_greet_line_number(self, nodes_by_name):
        """greet is defined on line 11 in the fixture."""
        node = nodes_by_name["greet"][0]
        assert node.line == 11, f"Expected line 11, got {node.line}"


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------


class TestClasses:
    def test_animal_extracted(self, nodes_by_name):
        assert "Animal" in nodes_by_name
        node = nodes_by_name["Animal"][0]
        assert node.kind == SymbolKind.CLASS

    def test_dog_extracted(self, nodes_by_name):
        assert "Dog" in nodes_by_name
        node = nodes_by_name["Dog"][0]
        assert node.kind == SymbolKind.CLASS

    def test_class_language(self, nodes_by_name):
        node = nodes_by_name["Animal"][0]
        assert node.language == "javascript"


# ---------------------------------------------------------------------------
# Methods
# ---------------------------------------------------------------------------


class TestMethods:
    def test_speak_is_method(self, nodes_by_name):
        speaks = nodes_by_name.get("speak", [])
        assert speaks, "speak should be extracted as a method"
        for n in speaks:
            assert n.kind == SymbolKind.METHOD

    def test_constructor_is_method(self, nodes_by_name):
        ctors = nodes_by_name.get("constructor", [])
        assert ctors, "constructor should be extracted"
        for n in ctors:
            assert n.kind == SymbolKind.METHOD

    def test_describe_is_method(self, nodes_by_name):
        assert "describe" in nodes_by_name
        node = nodes_by_name["describe"][0]
        assert node.kind == SymbolKind.METHOD

    def test_fetch_method_on_dog(self, nodes_by_name):
        """Dog.fetch is a method (distinct from the fetchData function)."""
        fetches = nodes_by_name.get("fetch", [])
        assert any(n.kind == SymbolKind.METHOD for n in fetches)

    def test_method_node_id_includes_class(self, nodes_by_name):
        speaks = nodes_by_name.get("speak", [])
        # Find Animal.speak specifically
        animal_speak = next((n for n in speaks if "Animal" in n.node_id), None)
        assert animal_speak is not None, "Expected Animal::speak node_id"


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


class TestInheritanceEdges:
    def test_dog_inherits_animal(self, parse_result, nodes_by_name):
        inherits = [e for e in parse_result.edges if e.kind == EdgeKind.INHERITS]
        assert inherits, "Expected INHERITS edges"
        dog_id = nodes_by_name["Dog"][0].node_id
        assert any(e.source_id == dog_id for e in inherits), "Dog should have an INHERITS edge"


class TestContainsEdges:
    def test_animal_contains_methods(self, parse_result, nodes_by_name):
        contains = [e for e in parse_result.edges if e.kind == EdgeKind.CONTAINS]
        assert contains, "Expected CONTAINS edges"
        animal_id = nodes_by_name["Animal"][0].node_id
        assert any(e.source_id == animal_id for e in contains), "Animal should CONTAIN its methods"


class TestImportEdges:
    def test_import_fs_promises(self, parse_result):
        """import { readFile } from 'fs/promises' should create an IMPORTS edge."""
        imports = [e for e in parse_result.edges if e.kind == EdgeKind.IMPORTS]
        assert imports, "Expected IMPORTS edges"
        assert any("fs/promises" in e.target_id or "fs" in e.target_id for e in imports), (
            "Expected IMPORTS edge for 'fs/promises'"
        )
