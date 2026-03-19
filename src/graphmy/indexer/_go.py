"""
graphmy/indexer/_go.py
======================
Go language parser using tree-sitter.

Extracts from .go files:
  - Top-level function declarations
  - Method declarations (functions with a receiver type — Go's way of adding
    behaviour to types, since Go has no classes)
  - Struct type declarations
  - Interface type declarations
  - Import declarations (single and grouped)

Go has no class inheritance — struct embedding (anonymous fields) is tracked
as a special kind of CONTAINS edge but is NOT modelled as INHERITS since Go
embedding is not subtype polymorphism.

Interface satisfaction is structural (implicit) in Go, so we cannot detect
IMPLEMENTS relationships statically without type checking. This is a known
limitation — graphmy only captures what the syntax reveals.
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_go as tsgo
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from graphmy.graph._model import Edge, EdgeKind, SymbolKind, SymbolNode
from graphmy.indexer._base import LanguageParser, ParseResult

_GO_LANGUAGE = Language(tsgo.language())
_PARSER = Parser(_GO_LANGUAGE)

# Top-level function declarations: func foo(args) returnType { }
_FUNC_QUERY = Query(
    _GO_LANGUAGE,
    """
    (function_declaration name: (identifier) @func.name) @func.def
    """,
)

# Method declarations: func (r ReceiverType) MethodName(args) returnType { }
# The receiver is a parameter_list with the concrete type inside.
_METHOD_QUERY = Query(
    _GO_LANGUAGE,
    """
    (method_declaration
      receiver: (parameter_list
        (parameter_declaration type: [(type_identifier)(pointer_type
          (type_identifier) @method.recv_type)]) @method.recv)
      name: (field_identifier) @method.name) @method.def
    """,
)

# Named type declarations: type Foo struct { } and type Bar interface { }
# Both are wrapped in type_declaration → type_spec.
_TYPE_QUERY = Query(
    _GO_LANGUAGE,
    """
    (type_declaration
      (type_spec
        name: (type_identifier) @type.name
        type: [(struct_type) (interface_type)] @type.body)) @type.def
    """,
)

# Import statements (single and grouped)
_IMPORT_QUERY = Query(
    _GO_LANGUAGE,
    """
    (import_spec path: (interpreted_string_literal) @import.path)
    """,
)

# Function call sites
_CALL_QUERY = Query(
    _GO_LANGUAGE,
    """
    (call_expression function: (identifier) @call.name)
    (call_expression function: (selector_expression
      field: (field_identifier) @call.name))
    """,
)


class GoParser(LanguageParser):
    """Parses Go source files using tree-sitter."""

    @property
    def extensions(self) -> tuple[str, ...]:
        return (".go",)

    @property
    def language_name(self) -> str:
        return "go"

    def parse(self, path: Path, source: str, project_root: Path) -> ParseResult:
        result = ParseResult()
        rel_file = self._rel_path(path, project_root)
        source_lines = source.splitlines()

        tree = _PARSER.parse(source.encode("utf-8"))
        root = tree.root_node

        # ------------------------------------------------------------------
        # Structs and interfaces (type declarations)
        # ------------------------------------------------------------------
        tc = QueryCursor(_TYPE_QUERY)
        t_caps = tc.captures(root)

        for type_def in t_caps.get("type.def", []):
            name_node = next(
                (n for n in t_caps.get("type.name", []) if self._is_descendant(n, type_def)), None
            )
            body_node = next(
                (n for n in t_caps.get("type.body", []) if self._is_descendant(n, type_def)), None
            )
            if not name_node or not name_node.text:
                continue

            type_name = name_node.text.decode("utf-8")
            node_id = self._make_node_id(rel_file, type_name)
            start_line = type_def.start_point[0] + 1
            end_line = type_def.end_point[0] + 1
            kind = (
                SymbolKind.INTERFACE
                if (body_node and body_node.type == "interface_type")
                else SymbolKind.STRUCT
            )

            result.nodes.append(
                SymbolNode(
                    node_id=node_id,
                    kind=kind,
                    name=type_name,
                    qualified=f"{rel_file}.{type_name}",
                    file=rel_file,
                    line=start_line,
                    end_line=end_line,
                    language=self.language_name,
                    docstring=self._extract_go_comment(type_def, source_lines),
                    signature=self._get_line(source_lines, start_line),
                    body=self._extract_body(source_lines, start_line, end_line),
                )
            )

        # ------------------------------------------------------------------
        # Top-level functions
        # ------------------------------------------------------------------
        fc = QueryCursor(_FUNC_QUERY)
        f_caps = fc.captures(root)

        for func_def in f_caps.get("func.def", []):
            name_node = next((n for n in f_caps.get("func.name", []) if n.parent == func_def), None)
            if not name_node or not name_node.text:
                continue
            func_name = name_node.text.decode("utf-8")
            node_id = self._make_node_id(rel_file, func_name)
            start_line = func_def.start_point[0] + 1
            end_line = func_def.end_point[0] + 1

            result.nodes.append(
                SymbolNode(
                    node_id=node_id,
                    kind=SymbolKind.FUNCTION,
                    name=func_name,
                    qualified=f"{rel_file}.{func_name}",
                    file=rel_file,
                    line=start_line,
                    end_line=end_line,
                    language=self.language_name,
                    docstring=self._extract_go_comment(func_def, source_lines),
                    signature=self._get_line(source_lines, start_line),
                    body=self._extract_body(source_lines, start_line, end_line),
                )
            )

        # ------------------------------------------------------------------
        # Method declarations (func (r Foo) Bar() {})
        # ------------------------------------------------------------------
        mc = QueryCursor(_METHOD_QUERY)
        m_caps = mc.captures(root)

        for method_def in m_caps.get("method.def", []):
            name_node = next(
                (n for n in m_caps.get("method.name", []) if self._is_descendant(n, method_def)),
                None,
            )
            recv_type_node = next(
                (
                    n
                    for n in m_caps.get("method.recv_type", [])
                    if self._is_descendant(n, method_def)
                ),
                None,
            )

            if not name_node or not name_node.text:
                continue

            method_name = name_node.text.decode("utf-8")

            # Receiver type: try to get from the pointer indirection or direct param
            recv_type = ""
            if recv_type_node and recv_type_node.text:
                recv_type = recv_type_node.text.decode("utf-8")
            else:
                # Fallback: scan the receiver parameter_list for a type_identifier
                recv_param = method_def.child_by_field_name("receiver")
                if recv_param:
                    for child in recv_param.children:
                        if child.type == "parameter_declaration":
                            for sub in child.children:
                                if sub.type in ("type_identifier", "pointer_type"):
                                    t = sub.text
                                    if t:
                                        recv_type = t.decode("utf-8").lstrip("*")
                                        break

            node_id = (
                self._make_node_id(rel_file, recv_type, method_name)
                if recv_type
                else self._make_node_id(rel_file, method_name)
            )
            start_line = method_def.start_point[0] + 1
            end_line = method_def.end_point[0] + 1

            node = SymbolNode(
                node_id=node_id,
                kind=SymbolKind.METHOD,
                name=method_name,
                qualified=f"{rel_file}.{recv_type}.{method_name}"
                if recv_type
                else f"{rel_file}.{method_name}",
                file=rel_file,
                line=start_line,
                end_line=end_line,
                language=self.language_name,
                docstring=self._extract_go_comment(method_def, source_lines),
                signature=self._get_line(source_lines, start_line),
                body=self._extract_body(source_lines, start_line, end_line),
            )
            result.nodes.append(node)

            # CONTAINS edge: receiver type (struct/interface) → method
            if recv_type:
                struct_node_id = self._make_node_id(rel_file, recv_type)
                result.edges.append(
                    Edge(
                        source_id=struct_node_id,
                        target_id=node_id,
                        kind=EdgeKind.CONTAINS,
                    )
                )

        # ------------------------------------------------------------------
        # Imports
        # ------------------------------------------------------------------
        ic = QueryCursor(_IMPORT_QUERY)
        i_caps = ic.captures(root)
        for imp_node in i_caps.get("import.path", []):
            module = imp_node.text.decode("utf-8").strip('"') if imp_node.text else ""
            if module:
                result.edges.append(
                    Edge(
                        source_id=rel_file,
                        target_id=self._make_external_id(module),
                        kind=EdgeKind.IMPORTS,
                    )
                )

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_go_comment(self, node: Node, source_lines: list[str]) -> str:
        """
        Extract the Go doc comment (// lines immediately preceding the node).
        Go doc comments are // single-line comments directly above declarations.
        """
        start_line = node.start_point[0]  # 0-indexed
        comment_lines: list[str] = []
        for i in range(start_line - 1, -1, -1):
            stripped = source_lines[i].strip()
            if stripped.startswith("//"):
                comment_lines.insert(0, stripped.lstrip("/ ").strip())
            else:
                break
        return " ".join(comment_lines)

    def _is_descendant(self, node: Node, ancestor: Node) -> bool:
        current = node.parent
        while current:
            if current == ancestor:
                return True
            current = current.parent
        return False

    def _get_line(self, source_lines: list[str], line_no: int) -> str:
        if 1 <= line_no <= len(source_lines):
            return source_lines[line_no - 1].strip()
        return ""

    def _extract_body(self, source_lines: list[str], start: int, end: int) -> str:
        if start < 1 or end < start:
            return ""
        return "\n".join(source_lines[start - 1 : end])
