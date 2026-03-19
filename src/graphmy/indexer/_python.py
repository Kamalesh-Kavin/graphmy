"""
graphmy/indexer/_python.py
==========================
Python language parser using tree-sitter.

Extracts from .py files:
  - Functions (sync and async, top-level and nested)
  - Classes (with base classes → INHERITS edges)
  - Methods (functions inside class bodies → CONTAINS edges)
  - Decorators (stored on the SymbolNode)
  - Imports (import X, from X import Y → IMPORTS edges)
  - Call sites (foo(), self.bar(), module.func() → CALLS edges, best-effort)
  - Docstrings (first string literal in a function/class body)

Call resolution is best-effort:
  We record a call to the short name (e.g. "validate_token"). The indexer's
  cross-file resolution pass later tries to match short names to fully-qualified
  node IDs. Unresolved calls become EXTERNAL stub nodes.
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from graphmy.graph._model import Edge, EdgeKind, SymbolKind, SymbolNode
from graphmy.indexer._base import LanguageParser, ParseResult

# ---------------------------------------------------------------------------
# Build the tree-sitter language object and parser once at module load.
# This is safe because tree-sitter Language objects are stateless and
# thread-safe — creating them once avoids repeated initialisation overhead.
# ---------------------------------------------------------------------------
_PY_LANGUAGE = Language(tspython.language())
_PARSER = Parser(_PY_LANGUAGE)

# ---------------------------------------------------------------------------
# Tree-sitter S-expression queries for Python constructs.
# Each query captures named nodes using @capture-name syntax.
# ---------------------------------------------------------------------------

# Captures all function definitions (sync and async).
# The 'async' modifier is a sibling of function_definition, not a separate node.
_FUNC_QUERY = Query(
    _PY_LANGUAGE,
    """
    (function_definition
      name: (identifier) @func.name) @func.def
    """,
)

# Captures class definitions with optional base classes.
_CLASS_QUERY = Query(
    _PY_LANGUAGE,
    """
    (class_definition
      name: (identifier) @class.name
      superclasses: (argument_list
        [(identifier) @class.base
         (attribute attribute: (identifier) @class.base)
        ]
      )?) @class.def
    """,
)

# Captures import statements.
# import foo, import foo.bar → module name captured
# from foo import bar, baz → module + names captured
_IMPORT_QUERY = Query(
    _PY_LANGUAGE,
    """
    (import_statement
      name: (dotted_name) @import.module)
    (import_from_statement
      module_name: (dotted_name) @import.from_module
      name: (dotted_name) @import.from_name)
    (import_from_statement
      module_name: (dotted_name) @import.from_module
      name: (aliased_import
        name: (dotted_name) @import.from_name))
    """,
)

# Captures function call sites.
# We capture the function expression that is being called so we can extract
# the short name or attribute chain.
_CALL_QUERY = Query(
    _PY_LANGUAGE,
    """
    (call function: (identifier) @call.name)
    (call function: (attribute attribute: (identifier) @call.name))
    """,
)


class PythonParser(LanguageParser):
    """
    Parses Python source files using tree-sitter.

    One instance is created by the registry and reused for all .py files.
    The tree-sitter Parser object is module-level so it is also shared.
    """

    @property
    def extensions(self) -> tuple[str, ...]:
        return (".py",)

    @property
    def language_name(self) -> str:
        return "python"

    def parse(self, path: Path, source: str, project_root: Path) -> ParseResult:
        """
        Parse a Python source file and extract all symbols and relationships.

        Strategy:
          1. Parse the full source into a tree-sitter CST.
          2. Run queries for classes, functions, imports, and calls.
          3. Build SymbolNodes and Edges from the query results.
          4. Resolve CONTAINS relationships (method is inside a class body).
          5. Best-effort call site resolution using name lookup.
        """
        result = ParseResult()
        rel_file = self._rel_path(path, project_root)
        source_lines = source.splitlines()

        # Parse the source into a tree-sitter concrete syntax tree.
        tree = _PARSER.parse(source.encode("utf-8"))
        root = tree.root_node

        # ------------------------------------------------------------------
        # Step 1 — Extract classes
        # ------------------------------------------------------------------
        class_nodes: dict[str, SymbolNode] = {}  # node_id → SymbolNode

        cursor = QueryCursor(_CLASS_QUERY)
        captures: dict[str, list[Node]] = cursor.captures(root)

        # Group captures by match: each class.def node corresponds to one class
        # Iterate by pairing class.def with its class.name sibling
        class_def_nodes = captures.get("class.def", [])
        class_name_nodes = captures.get("class.name", [])
        class_base_nodes = captures.get("class.base", [])

        # Build a mapping from class.def node → its name node
        # tree-sitter captures are ordered by byte position
        # We match them by finding the name node that is a child of each def node
        for class_def in class_def_nodes:
            # Find the corresponding name node (direct child of class_def)
            name_node = next(
                (n for n in class_name_nodes if n.parent == class_def),
                None,
            )
            if name_node is None:
                continue

            class_name = name_node.text.decode("utf-8") if name_node.text else ""
            if not class_name:
                continue

            node_id = self._make_node_id(rel_file, class_name)
            start_line = class_def.start_point[0] + 1  # tree-sitter is 0-indexed
            end_line = class_def.end_point[0] + 1

            # Extract docstring from the class body (first string expr child).
            docstring = self._extract_docstring(class_def, source_lines)

            # Extract base classes for this specific class_def node.
            bases = [
                n.text.decode("utf-8")
                for n in class_base_nodes
                if n.text and self._is_descendant(n, class_def)
            ]

            # Determine if this is a top-level class or nested (for qualified name).
            parent_class = self._find_parent_class(class_def)
            if parent_class:
                parent_name_node = parent_class.child_by_field_name("name")
                parent_name = (
                    parent_name_node.text.decode("utf-8")
                    if parent_name_node and parent_name_node.text
                    else ""
                )
                qualified = (
                    f"{rel_file.replace('/', '.').removesuffix('.py')}.{parent_name}.{class_name}"
                )
            else:
                qualified = f"{rel_file.replace('/', '.').removesuffix('.py')}.{class_name}"

            node = SymbolNode(
                node_id=node_id,
                kind=SymbolKind.CLASS,
                name=class_name,
                qualified=qualified,
                file=rel_file,
                line=start_line,
                end_line=end_line,
                language=self.language_name,
                docstring=docstring,
                signature=self._get_line(source_lines, start_line),
                body=self._extract_body(source_lines, start_line, end_line),
            )
            class_nodes[node_id] = node
            result.nodes.append(node)

            # INHERITS edges for each base class.
            for base in bases:
                # We record the base class name; cross-file resolution happens later.
                target_id = self._make_node_id(rel_file, base)
                result.edges.append(
                    Edge(
                        source_id=node_id,
                        target_id=target_id,
                        kind=EdgeKind.INHERITS,
                    )
                )

        # ------------------------------------------------------------------
        # Step 2 — Extract functions and methods
        # ------------------------------------------------------------------
        func_cursor = QueryCursor(_FUNC_QUERY)
        func_captures: dict[str, list[Node]] = func_cursor.captures(root)
        func_def_nodes = func_captures.get("func.def", [])
        func_name_nodes = func_captures.get("func.name", [])

        for func_def in func_def_nodes:
            name_node = next(
                (n for n in func_name_nodes if n.parent == func_def),
                None,
            )
            if name_node is None:
                continue

            func_name = name_node.text.decode("utf-8") if name_node.text else ""
            if not func_name:
                continue

            start_line = func_def.start_point[0] + 1
            end_line = func_def.end_point[0] + 1
            docstring = self._extract_docstring(func_def, source_lines)
            is_async = self._is_async(func_def)
            decorators = self._extract_decorators(func_def, source_lines)
            signature = self._get_line(source_lines, start_line)
            body = self._extract_body(source_lines, start_line, end_line)

            # Is this function inside a class body? → it's a method.
            parent_class_def = self._find_parent_class(func_def)
            if parent_class_def:
                parent_name_node = parent_class_def.child_by_field_name("name")
                parent_class_name = (
                    parent_name_node.text.decode("utf-8")
                    if parent_name_node and parent_name_node.text
                    else "Unknown"
                )
                node_id = self._make_node_id(rel_file, parent_class_name, func_name)
                qualified = (
                    f"{rel_file.replace('/', '.').removesuffix('.py')}"
                    f".{parent_class_name}.{func_name}"
                )
                kind = SymbolKind.METHOD
                class_node_id = self._make_node_id(rel_file, parent_class_name)

                # CONTAINS edge: class → method
                result.edges.append(
                    Edge(
                        source_id=class_node_id,
                        target_id=node_id,
                        kind=EdgeKind.CONTAINS,
                    )
                )
            else:
                node_id = self._make_node_id(rel_file, func_name)
                qualified = f"{rel_file.replace('/', '.').removesuffix('.py')}.{func_name}"
                kind = SymbolKind.FUNCTION

            node = SymbolNode(
                node_id=node_id,
                kind=kind,
                name=func_name,
                qualified=qualified,
                file=rel_file,
                line=start_line,
                end_line=end_line,
                language=self.language_name,
                docstring=docstring,
                signature=signature,
                body=body,
                is_async=is_async,
                decorators=decorators,
            )
            result.nodes.append(node)

        # ------------------------------------------------------------------
        # Step 3 — Extract imports → IMPORTS edges
        # ------------------------------------------------------------------
        import_cursor = QueryCursor(_IMPORT_QUERY)
        import_captures: dict[str, list[Node]] = import_cursor.captures(root)

        for import_node in import_captures.get("import.module", []):
            module_name = import_node.text.decode("utf-8") if import_node.text else ""
            if module_name:
                target_id = self._make_external_id(module_name)
                result.edges.append(
                    Edge(
                        source_id=rel_file,
                        target_id=target_id,
                        kind=EdgeKind.IMPORTS,
                    )
                )

        for import_node in import_captures.get("import.from_module", []):
            module_name = import_node.text.decode("utf-8") if import_node.text else ""
            if module_name:
                target_id = self._make_external_id(module_name)
                result.edges.append(
                    Edge(
                        source_id=rel_file,
                        target_id=target_id,
                        kind=EdgeKind.IMPORTS,
                    )
                )

        # ------------------------------------------------------------------
        # Step 4 — Extract call sites → CALLS edges (best-effort)
        # ------------------------------------------------------------------
        call_cursor = QueryCursor(_CALL_QUERY)
        call_captures: dict[str, list[Node]] = call_cursor.captures(root)

        for call_name_node in call_captures.get("call.name", []):
            called_name = call_name_node.text.decode("utf-8") if call_name_node.text else ""
            if not called_name or called_name in _PYTHON_BUILTINS:
                continue

            # Find the enclosing function/method to use as the source of the CALLS edge.
            caller_id = self._find_enclosing_function_id(call_name_node, rel_file, result.nodes)
            if caller_id is None:
                # Call is at module scope — use the FILE node as caller.
                caller_id = rel_file

            # Target is best-effort: try to match to a known node, else make external.
            target_id = self._resolve_call_target(called_name, rel_file, result.nodes)

            result.edges.append(
                Edge(
                    source_id=caller_id,
                    target_id=target_id,
                    kind=EdgeKind.CALLS,
                )
            )

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_docstring(self, node: Node, source_lines: list[str]) -> str:
        """
        Extract the first docstring from a function or class body.

        In Python, a docstring is the first statement in a body if it is an
        expression statement containing a string literal.
        """
        body_node = node.child_by_field_name("body")
        if body_node is None:
            return ""
        for child in body_node.children:
            if child.type == "expression_statement":
                for subchild in child.children:
                    if subchild.type in ("string", "concatenated_string"):
                        text = subchild.text
                        if text:
                            raw = text.decode("utf-8").strip()
                            # Strip quotes (''', """, ', ")
                            for q in ('"""', "'''", '"', "'"):
                                if raw.startswith(q) and raw.endswith(q):
                                    return raw[len(q) : -len(q)].strip()
                            return raw
            break  # only check the first statement
        return ""

    def _is_async(self, func_def: Node) -> bool:
        """True if the function_definition node represents an async function.

        In tree-sitter Python >=0.23, the 'async' keyword is a direct
        anonymous child of the function_definition node itself (not a sibling).
        Example CST:
            (function_definition
              (async)
              (def)
              name: (identifier) ...)
        """
        return any(child.type == "async" for child in func_def.children)

    def _extract_decorators(self, func_def: Node, source_lines: list[str]) -> list[str]:
        """
        Extract decorator names from the decorated_definition parent node, if any.
        """
        parent = func_def.parent
        if parent is None or parent.type != "decorated_definition":
            return []
        decorators = []
        for child in parent.children:
            if child.type == "decorator":
                # Get the text of the decorator (strip the leading @)
                text = child.text
                if text:
                    dec = text.decode("utf-8").lstrip("@").strip().split("(")[0]
                    decorators.append(dec)
        return decorators

    def _find_parent_class(self, node: Node) -> Node | None:
        """
        Walk up the CST to find the nearest enclosing class_definition node.
        Returns None if the node is not inside a class body.
        """
        current = node.parent
        while current is not None:
            if current.type == "class_definition":
                return current
            # Stop if we hit a function_definition — nested functions inside
            # functions are NOT methods.
            if current.type == "function_definition":
                return None
            current = current.parent
        return None

    def _find_enclosing_function_id(
        self,
        call_node: Node,
        rel_file: str,
        nodes: list[SymbolNode],
    ) -> str | None:
        """
        Find the node_id of the innermost function/method that contains call_node.

        Returns None if the call is at module scope.
        """
        current = call_node.parent
        while current is not None:
            if current.type == "function_definition":
                name_node = current.child_by_field_name("name")
                if name_node and name_node.text:
                    func_name = name_node.text.decode("utf-8")
                    parent_class = self._find_parent_class(current)
                    if parent_class:
                        cls_name_node = parent_class.child_by_field_name("name")
                        cls_name = (
                            cls_name_node.text.decode("utf-8")
                            if cls_name_node and cls_name_node.text
                            else ""
                        )
                        return self._make_node_id(rel_file, cls_name, func_name)
                    return self._make_node_id(rel_file, func_name)
            current = current.parent
        return None

    def _resolve_call_target(
        self,
        called_name: str,
        rel_file: str,
        nodes: list[SymbolNode],
    ) -> str:
        """
        Try to resolve a call target name to a known node_id in this file.

        If we find a node with the matching short name in this file, use its
        node_id. Otherwise, create an external stub (cross-file resolution
        happens in a separate pass by the indexer).
        """
        for node in nodes:
            if node.name == called_name:
                return node.node_id
        # Not found in this file — record as a local-name stub.
        # The indexer's cross-file pass will try to match this later.
        return self._make_external_id("__unresolved__", called_name)

    def _is_descendant(self, node: Node, ancestor: Node) -> bool:
        """True if `node` is a descendant of `ancestor` in the CST."""
        current = node.parent
        while current is not None:
            if current == ancestor:
                return True
            current = current.parent
        return False

    def _get_line(self, source_lines: list[str], line_no: int) -> str:
        """Return the source line at 1-indexed line_no, or empty string."""
        if 1 <= line_no <= len(source_lines):
            return source_lines[line_no - 1].strip()
        return ""

    def _extract_body(self, source_lines: list[str], start: int, end: int) -> str:
        """Return the source lines from start to end (1-indexed, inclusive)."""
        if start < 1 or end < start:
            return ""
        return "\n".join(source_lines[start - 1 : end])


# ---------------------------------------------------------------------------
# Python built-in names we skip when recording call edges — these are never
# user-defined symbols and would just add noise to the graph.
# ---------------------------------------------------------------------------
_PYTHON_BUILTINS: frozenset[str] = frozenset(
    {
        "print",
        "len",
        "range",
        "enumerate",
        "zip",
        "map",
        "filter",
        "sorted",
        "reversed",
        "list",
        "dict",
        "set",
        "tuple",
        "str",
        "int",
        "float",
        "bool",
        "bytes",
        "type",
        "isinstance",
        "issubclass",
        "hasattr",
        "getattr",
        "setattr",
        "delattr",
        "repr",
        "super",
        "property",
        "staticmethod",
        "classmethod",
        "abs",
        "round",
        "min",
        "max",
        "sum",
        "any",
        "all",
        "open",
        "iter",
        "next",
        "vars",
        "dir",
        "id",
        "hash",
        "callable",
        "format",
        "input",
        "hex",
        "oct",
        "bin",
        "chr",
        "ord",
        "divmod",
        "pow",
        "eval",
        "exec",
        "compile",
        "globals",
        "locals",
        "NotImplemented",
        "Ellipsis",
        "__import__",
    }
)
