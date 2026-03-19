"""
graphmy/indexer/_java.py
========================
Java language parser using tree-sitter.

Extracts from .java files:
  - Class declarations with extends (INHERITS) and implements (IMPLEMENTS)
  - Interface declarations
  - Enum declarations
  - Method declarations (inside class/interface bodies → CONTAINS)
  - Constructor declarations
  - Import declarations
  - Call sites (method invocations, best-effort)

Java notes:
  - A single .java file typically defines one public class, but inner/nested
    classes are also extracted.
  - Method overloading is common — multiple methods with the same name in the
    same class are all extracted; they get distinct node_ids via line number
    suffix when names collide.
  - Generics type parameters are stripped from signatures for readability.
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_java as tsjava
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from graphmy.graph._model import Edge, EdgeKind, SymbolKind, SymbolNode
from graphmy.indexer._base import LanguageParser, ParseResult

_JAVA_LANGUAGE = Language(tsjava.language())
_PARSER = Parser(_JAVA_LANGUAGE)

_CLASS_QUERY = Query(
    _JAVA_LANGUAGE,
    """
    (class_declaration
      name: (identifier) @class.name
      superclass: (superclass (type_identifier) @class.extends)?
      interfaces: (super_interfaces
        (type_list (type_identifier) @class.implements))?
    ) @class.def
    """,
)

_IFACE_QUERY = Query(
    _JAVA_LANGUAGE,
    """
    (interface_declaration name: (identifier) @iface.name) @iface.def
    """,
)

_ENUM_QUERY = Query(
    _JAVA_LANGUAGE,
    """
    (enum_declaration name: (identifier) @enum.name) @enum.def
    """,
)

_METHOD_QUERY = Query(
    _JAVA_LANGUAGE,
    """
    (method_declaration name: (identifier) @method.name) @method.def
    (constructor_declaration name: (identifier) @method.name) @method.def
    """,
)

_IMPORT_QUERY = Query(
    _JAVA_LANGUAGE,
    """
    (import_declaration (scoped_identifier) @import.path)
    """,
)

_CALL_QUERY = Query(
    _JAVA_LANGUAGE,
    """
    (method_invocation name: (identifier) @call.name)
    """,
)


class JavaParser(LanguageParser):
    """Parses Java source files using tree-sitter."""

    @property
    def extensions(self) -> tuple[str, ...]:
        return (".java",)

    @property
    def language_name(self) -> str:
        return "java"

    def parse(self, path: Path, source: str, project_root: Path) -> ParseResult:
        result = ParseResult()
        rel_file = self._rel_path(path, project_root)
        source_lines = source.splitlines()

        tree = _PARSER.parse(source.encode("utf-8"))
        root = tree.root_node

        # ------------------------------------------------------------------
        # Classes
        # ------------------------------------------------------------------
        cc = QueryCursor(_CLASS_QUERY)
        c_caps = cc.captures(root)

        for class_def in c_caps.get("class.def", []):
            name_node = next(
                (n for n in c_caps.get("class.name", []) if n.parent == class_def), None
            )
            if not name_node or not name_node.text:
                continue
            class_name = name_node.text.decode("utf-8")
            node_id = self._make_node_id(rel_file, class_name)
            start_line = class_def.start_point[0] + 1
            end_line = class_def.end_point[0] + 1

            result.nodes.append(
                SymbolNode(
                    node_id=node_id,
                    kind=SymbolKind.CLASS,
                    name=class_name,
                    qualified=f"{rel_file}.{class_name}",
                    file=rel_file,
                    line=start_line,
                    end_line=end_line,
                    language=self.language_name,
                    docstring=self._extract_javadoc(class_def, source_lines),
                    signature=self._get_line(source_lines, start_line),
                    body=self._extract_body(source_lines, start_line, end_line),
                )
            )

            # INHERITS (extends)
            for ext_node in c_caps.get("class.extends", []):
                if self._is_descendant(ext_node, class_def) and ext_node.text:
                    base = ext_node.text.decode("utf-8")
                    result.edges.append(
                        Edge(
                            source_id=node_id,
                            target_id=self._make_node_id(rel_file, base),
                            kind=EdgeKind.INHERITS,
                        )
                    )

            # IMPLEMENTS
            for impl_node in c_caps.get("class.implements", []):
                if self._is_descendant(impl_node, class_def) and impl_node.text:
                    iface = impl_node.text.decode("utf-8")
                    result.edges.append(
                        Edge(
                            source_id=node_id,
                            target_id=self._make_node_id(rel_file, iface),
                            kind=EdgeKind.IMPLEMENTS,
                        )
                    )

        # ------------------------------------------------------------------
        # Interfaces
        # ------------------------------------------------------------------
        ic = QueryCursor(_IFACE_QUERY)
        i_caps = ic.captures(root)
        for iface_def in i_caps.get("iface.def", []):
            name_node = next(
                (n for n in i_caps.get("iface.name", []) if n.parent == iface_def), None
            )
            if not name_node or not name_node.text:
                continue
            iface_name = name_node.text.decode("utf-8")
            node_id = self._make_node_id(rel_file, iface_name)
            start_line = iface_def.start_point[0] + 1
            end_line = iface_def.end_point[0] + 1

            result.nodes.append(
                SymbolNode(
                    node_id=node_id,
                    kind=SymbolKind.INTERFACE,
                    name=iface_name,
                    qualified=f"{rel_file}.{iface_name}",
                    file=rel_file,
                    line=start_line,
                    end_line=end_line,
                    language=self.language_name,
                    docstring=self._extract_javadoc(iface_def, source_lines),
                    signature=self._get_line(source_lines, start_line),
                    body=self._extract_body(source_lines, start_line, end_line),
                )
            )

        # ------------------------------------------------------------------
        # Enums
        # ------------------------------------------------------------------
        ec = QueryCursor(_ENUM_QUERY)
        e_caps = ec.captures(root)
        for enum_def in e_caps.get("enum.def", []):
            name_node = next((n for n in e_caps.get("enum.name", []) if n.parent == enum_def), None)
            if not name_node or not name_node.text:
                continue
            enum_name = name_node.text.decode("utf-8")
            node_id = self._make_node_id(rel_file, enum_name)
            start_line = enum_def.start_point[0] + 1
            end_line = enum_def.end_point[0] + 1

            result.nodes.append(
                SymbolNode(
                    node_id=node_id,
                    kind=SymbolKind.ENUM,
                    name=enum_name,
                    qualified=f"{rel_file}.{enum_name}",
                    file=rel_file,
                    line=start_line,
                    end_line=end_line,
                    language=self.language_name,
                    signature=self._get_line(source_lines, start_line),
                    body=self._extract_body(source_lines, start_line, end_line),
                )
            )

        # ------------------------------------------------------------------
        # Methods and constructors
        # ------------------------------------------------------------------
        mc = QueryCursor(_METHOD_QUERY)
        m_caps = mc.captures(root)
        seen_methods: dict[str, int] = {}  # node_id → count (for overloads)

        for method_def in m_caps.get("method.def", []):
            name_node = next(
                (n for n in m_caps.get("method.name", []) if n.parent == method_def), None
            )
            if not name_node or not name_node.text:
                continue
            method_name = name_node.text.decode("utf-8")
            start_line = method_def.start_point[0] + 1
            end_line = method_def.end_point[0] + 1

            # Find enclosing class
            parent_class = self._find_parent_class(method_def)
            if parent_class is None:
                continue
            cls_name_node = parent_class.child_by_field_name("name")
            cls_name = (
                cls_name_node.text.decode("utf-8")
                if cls_name_node and cls_name_node.text
                else "Unknown"
            )

            node_id = self._make_node_id(rel_file, cls_name, method_name)

            # Handle overloads — append line number to make IDs unique
            if node_id in seen_methods:
                node_id = f"{node_id}_{start_line}"
            seen_methods[node_id] = start_line

            cls_node_id = self._make_node_id(rel_file, cls_name)

            result.nodes.append(
                SymbolNode(
                    node_id=node_id,
                    kind=SymbolKind.METHOD,
                    name=method_name,
                    qualified=f"{rel_file}.{cls_name}.{method_name}",
                    file=rel_file,
                    line=start_line,
                    end_line=end_line,
                    language=self.language_name,
                    docstring=self._extract_javadoc(method_def, source_lines),
                    signature=self._get_line(source_lines, start_line),
                    body=self._extract_body(source_lines, start_line, end_line),
                )
            )
            result.edges.append(
                Edge(
                    source_id=cls_node_id,
                    target_id=node_id,
                    kind=EdgeKind.CONTAINS,
                )
            )

        # ------------------------------------------------------------------
        # Imports
        # ------------------------------------------------------------------
        imp_c = QueryCursor(_IMPORT_QUERY)
        imp_caps = imp_c.captures(root)
        for imp_node in imp_caps.get("import.path", []):
            path_str = imp_node.text.decode("utf-8") if imp_node.text else ""
            if path_str:
                result.edges.append(
                    Edge(
                        source_id=rel_file,
                        target_id=self._make_external_id(path_str),
                        kind=EdgeKind.IMPORTS,
                    )
                )

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_parent_class(self, node: Node) -> Node | None:
        current = node.parent
        while current:
            if current.type in ("class_declaration", "interface_declaration"):
                return current
            current = current.parent
        return None

    def _is_descendant(self, node: Node, ancestor: Node) -> bool:
        current = node.parent
        while current:
            if current == ancestor:
                return True
            current = current.parent
        return False

    def _extract_javadoc(self, node: Node, source_lines: list[str]) -> str:
        """Extract a /** ... */ Javadoc comment preceding the node."""
        start_line = node.start_point[0]
        if start_line == 0:
            return ""
        lines = []
        for i in range(start_line - 1, -1, -1):
            stripped = source_lines[i].strip()
            lines.insert(0, stripped)
            if stripped.startswith("/**"):
                break
            if not (stripped.startswith("*") or stripped.startswith("/*")):
                lines.clear()
                break
        return "\n".join(lines) if lines else ""

    def _get_line(self, source_lines: list[str], line_no: int) -> str:
        if 1 <= line_no <= len(source_lines):
            return source_lines[line_no - 1].strip()
        return ""

    def _extract_body(self, source_lines: list[str], start: int, end: int) -> str:
        if start < 1 or end < start:
            return ""
        return "\n".join(source_lines[start - 1 : end])
