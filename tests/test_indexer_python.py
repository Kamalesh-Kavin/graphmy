"""
tests/test_indexer_python.py
============================
Unit tests for the Python language parser (PythonParser).

We parse the tests/fixtures/sample_python/app.py fixture and assert that:
  - Top-level functions are extracted with correct names and kinds
  - Classes are extracted with correct names
  - Methods inside classes are extracted as METHOD kind
  - Inheritance edges (INHERITS) are recorded
  - CONTAINS edges link classes to their methods
  - Async functions are flagged correctly
  - Decorators are captured
  - Docstrings are extracted from functions and classes
  - CALLS edges are recorded for intra-file calls

All assertions operate on ParseResult — the raw output of the parser before
the indexer applies cross-file resolution. This keeps the test fast (no full
index build) and laser-focused on the parser's correctness.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from graphmy.graph._model import EdgeKind, SymbolKind
from graphmy.indexer._python import PythonParser

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample_python"
FIXTURE_FILE = FIXTURE_DIR / "app.py"


@pytest.fixture(scope="module")
def parse_result():
    """
    Parse the sample Python fixture once (module scope) and return the result.

    Module scope means we pay the tree-sitter parse cost only once per
    test session, which keeps the full suite fast.
    """
    parser = PythonParser()
    source = FIXTURE_FILE.read_text(encoding="utf-8")
    return parser.parse(FIXTURE_FILE, source, FIXTURE_DIR)


@pytest.fixture(scope="module")
def nodes_by_name(parse_result):
    """Helper: dict mapping short name → list[SymbolNode]."""
    result: dict[str, list] = {}
    for node in parse_result.nodes:
        result.setdefault(node.name, []).append(node)
    return result


@pytest.fixture(scope="module")
def edges_by_kind(parse_result):
    """Helper: dict mapping EdgeKind → list[Edge]."""
    result: dict[EdgeKind, list] = {}
    for edge in parse_result.edges:
        result.setdefault(edge.kind, []).append(edge)
    return result


# ---------------------------------------------------------------------------
# Node extraction tests
# ---------------------------------------------------------------------------


class TestFunctions:
    """Top-level functions must be parsed as FUNCTION kind."""

    def test_greet_extracted(self, nodes_by_name):
        assert "greet" in nodes_by_name, "greet function should be extracted"
        node = nodes_by_name["greet"][0]
        assert node.kind == SymbolKind.FUNCTION

    def test_fetch_data_extracted(self, nodes_by_name):
        assert "fetch_data" in nodes_by_name
        node = nodes_by_name["fetch_data"][0]
        assert node.kind == SymbolKind.FUNCTION

    def test_private_helper_extracted(self, nodes_by_name):
        """Private functions (leading underscore) must still be indexed."""
        assert "_private_helper" in nodes_by_name
        node = nodes_by_name["_private_helper"][0]
        assert node.kind == SymbolKind.FUNCTION

    def test_decorated_function_extracted(self, nodes_by_name):
        assert "decorated_function" in nodes_by_name
        node = nodes_by_name["decorated_function"][0]
        assert node.kind == SymbolKind.FUNCTION

    def test_function_language_is_python(self, nodes_by_name):
        node = nodes_by_name["greet"][0]
        assert node.language == "python"

    def test_function_file_is_relative(self, nodes_by_name):
        """file field must be relative to project_root, not absolute."""
        node = nodes_by_name["greet"][0]
        assert not Path(node.file).is_absolute(), f"Expected relative path, got {node.file!r}"
        assert node.file.endswith("app.py")

    def test_function_line_numbers(self, nodes_by_name):
        """greet is on line 22 in the fixture — parser must record this."""
        node = nodes_by_name["greet"][0]
        assert node.line == 22, f"Expected line 22, got {node.line}"

    def test_node_id_format(self, nodes_by_name):
        """node_id must follow the '<rel_file>::<name>' pattern."""
        node = nodes_by_name["greet"][0]
        assert "::" in node.node_id
        assert "greet" in node.node_id


class TestAsyncFunction:
    """fetch_data is declared async — is_async must be True."""

    def test_fetch_data_is_async(self, nodes_by_name):
        node = nodes_by_name["fetch_data"][0]
        assert node.is_async is True

    def test_greet_is_not_async(self, nodes_by_name):
        node = nodes_by_name["greet"][0]
        assert node.is_async is False


class TestClasses:
    """Classes must be extracted with SymbolKind.CLASS."""

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
        assert node.language == "python"

    def test_animal_has_docstring(self, nodes_by_name):
        node = nodes_by_name["Animal"][0]
        assert node.docstring, "Animal class should have a docstring"
        assert "animal" in node.docstring.lower()


class TestMethods:
    """Methods inside classes must be extracted as SymbolKind.METHOD."""

    def test_speak_is_method(self, nodes_by_name):
        # Both Animal and Dog define speak — find the Animal one.
        speaks = nodes_by_name.get("speak", [])
        assert len(speaks) >= 1
        for n in speaks:
            assert n.kind == SymbolKind.METHOD, f"speak should be METHOD, got {n.kind}"

    def test_init_is_method(self, nodes_by_name):
        inits = nodes_by_name.get("__init__", [])
        assert inits, "__init__ methods should be extracted"
        for n in inits:
            assert n.kind == SymbolKind.METHOD

    def test_describe_method_exists(self, nodes_by_name):
        assert "describe" in nodes_by_name
        node = nodes_by_name["describe"][0]
        assert node.kind == SymbolKind.METHOD

    def test_fetch_method_on_dog(self, nodes_by_name):
        """Dog.fetch is a method. Make sure it is distinct from the fetch_data fn."""
        fetches = nodes_by_name.get("fetch", [])
        assert any(n.kind == SymbolKind.METHOD for n in fetches)

    def test_method_node_id_includes_class(self, nodes_by_name):
        """METHOD node_id must include the class name: file::ClassName::method."""
        # Find Animal.speak specifically
        speaks = nodes_by_name.get("speak", [])
        animal_speak = next(
            (n for n in speaks if "Animal" in n.node_id),
            None,
        )
        assert animal_speak is not None, "Expected Animal::speak in node_ids"


class TestDocstrings:
    """Docstrings must be extracted from the first string literal in a body."""

    def test_greet_has_docstring(self, nodes_by_name):
        node = nodes_by_name["greet"][0]
        assert node.docstring == "Return a greeting message."

    def test_fetch_data_has_docstring(self, nodes_by_name):
        node = nodes_by_name["fetch_data"][0]
        assert node.docstring, "fetch_data should have a docstring"
        assert "Fetch data" in node.docstring

    def test_private_helper_has_docstring(self, nodes_by_name):
        node = nodes_by_name["_private_helper"][0]
        assert "Internal helper" in node.docstring


class TestDecorators:
    """Decorated functions must have their decorator names recorded."""

    def test_decorated_function_has_decorator(self, nodes_by_name):
        node = nodes_by_name["decorated_function"][0]
        assert node.decorators, "decorated_function should have decorators list"
        assert "my_decorator" in node.decorators


class TestSignatureAndBody:
    """Signature (first line of def) and body (full source) must be captured."""

    def test_greet_signature(self, nodes_by_name):
        node = nodes_by_name["greet"][0]
        assert "def greet" in node.signature

    def test_greet_body_not_empty(self, nodes_by_name):
        node = nodes_by_name["greet"][0]
        assert node.body, "Body should not be empty"
        assert "def greet" in node.body  # body includes the def line


# ---------------------------------------------------------------------------
# Edge tests
# ---------------------------------------------------------------------------


class TestInheritanceEdges:
    """Dog(Animal) must produce an INHERITS edge Dog → Animal."""

    def test_dog_inherits_animal(self, parse_result, nodes_by_name):
        inherits_edges = [e for e in parse_result.edges if e.kind == EdgeKind.INHERITS]
        assert inherits_edges, "Expected at least one INHERITS edge"

        dog_node = nodes_by_name["Dog"][0]
        assert any(e.source_id == dog_node.node_id for e in inherits_edges), (
            "Dog should have an INHERITS edge as source"
        )


class TestContainsEdges:
    """Class → Method must produce CONTAINS edges."""

    def test_animal_contains_speak(self, parse_result, nodes_by_name):
        contains = [e for e in parse_result.edges if e.kind == EdgeKind.CONTAINS]
        assert contains, "Expected CONTAINS edges from classes to methods"

        animal_id = nodes_by_name["Animal"][0].node_id
        assert any(e.source_id == animal_id for e in contains), (
            "Animal should have CONTAINS edges to its methods"
        )


class TestCallsEdges:
    """fetch_data calls greet → must produce a CALLS edge."""

    def test_fetch_data_calls_greet(self, parse_result, nodes_by_name):
        calls_edges = [e for e in parse_result.edges if e.kind == EdgeKind.CALLS]
        assert calls_edges, "Expected at least one CALLS edge"

        fetch_data_id = nodes_by_name["fetch_data"][0].node_id
        greet_id = nodes_by_name["greet"][0].node_id
        assert any(e.source_id == fetch_data_id and e.target_id == greet_id for e in calls_edges), (
            "fetch_data should have a CALLS edge to greet"
        )


class TestImportEdges:
    """Import statements must produce IMPORTS edges from the file node."""

    def test_imports_os(self, parse_result):
        imports = [e for e in parse_result.edges if e.kind == EdgeKind.IMPORTS]
        assert imports, "Expected IMPORTS edges"
        # 'import os' should produce an edge to ext::os
        assert any("os" in e.target_id for e in imports), "Expected an IMPORTS edge for 'os'"

    def test_imports_sys(self, parse_result):
        imports = [e for e in parse_result.edges if e.kind == EdgeKind.IMPORTS]
        assert any("sys" in e.target_id for e in imports), "Expected an IMPORTS edge for 'sys'"
