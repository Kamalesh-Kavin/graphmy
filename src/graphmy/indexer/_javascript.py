"""
graphmy/indexer/_javascript.py
===============================
JavaScript and TypeScript language parser using tree-sitter.

Handles:  .js  .mjs  .cjs  .ts  .tsx

Extracts:
  - Function declarations, function expressions, arrow functions bound to variables
  - Class declarations with extends (INHERITS) and implements (TypeScript, IMPLEMENTS)
  - Method definitions inside classes (CONTAINS)
  - Interface declarations (TypeScript)
  - Enum declarations (TypeScript)
  - Import statements (ESM import ... from, CommonJS require())
  - Call sites (best-effort, captures identifier calls and method calls)

TypeScript note:
  Both JS and TS use the same parser instance. The grammar package
  `tree-sitter-typescript` provides two separate grammars:
    - language_typescript() — for .ts files
    - language_tsx()        — for .tsx files
  JavaScript uses tree-sitter-javascript.
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_javascript as tsjs
from tree_sitter import Language, Node, Parser, Query, QueryCursor
from tree_sitter_typescript import language_tsx, language_typescript

from graphmy.graph._model import Edge, EdgeKind, SymbolKind, SymbolNode
from graphmy.indexer._base import LanguageParser, ParseResult

# ---------------------------------------------------------------------------
# Build language objects and parsers once at module load.
# ---------------------------------------------------------------------------
_JS_LANGUAGE = Language(tsjs.language())
_TS_LANGUAGE = Language(language_typescript())
_TSX_LANGUAGE = Language(language_tsx())

_JS_PARSER = Parser(_JS_LANGUAGE)
_TS_PARSER = Parser(_TS_LANGUAGE)
_TSX_PARSER = Parser(_TSX_LANGUAGE)

# ---------------------------------------------------------------------------
# Query pattern strings — stored separately so we can construct Query objects
# for any language (JS, TS, TSX) without needing Query.pattern attribute
# which is not available in tree-sitter 0.25.x.
# ---------------------------------------------------------------------------

# Function declarations: function foo() {}
_FUNC_DECL_PATTERN = """
    (function_declaration name: (identifier) @func.name) @func.def
    (generator_function_declaration name: (identifier) @func.name) @func.def
    """

# Arrow/expression functions bound to const/let/var:  const foo = () => {}
_ARROW_PATTERN = """
    (lexical_declaration
      (variable_declarator
        name: (identifier) @func.name
        value: [(arrow_function) (function_expression)] @func.def))
    (variable_declaration
      (variable_declarator
        name: (identifier) @func.name
        value: [(arrow_function) (function_expression)] @func.def))
    """

# Class declarations with optional extends
_CLASS_PATTERN_JS = """
    (class_declaration
      name: (identifier) @class.name
      (class_heritage (identifier) @class.base)?) @class.def
    """

# Method definitions inside a class body
_METHOD_PATTERN = """
    (method_definition name: (property_identifier) @method.name) @method.def
    """

# ESM imports: import foo from '...', import { bar } from '...'
_IMPORT_PATTERN = """
    (import_statement source: (string) @import.source)
    """

# CommonJS require(): const x = require('...')
_REQUIRE_PATTERN = """
    (call_expression
      function: (identifier) @fn (#eq? @fn "require")
      arguments: (arguments (string) @import.source))
    """

# Call sites
_CALL_PATTERN = """
    (call_expression function: (identifier) @call.name)
    (call_expression function: (member_expression
      property: (property_identifier) @call.name))
    """

# TypeScript-specific: interface declarations
_IFACE_PATTERN_TS = """
    (interface_declaration name: (type_identifier) @iface.name) @iface.def
    """

# TypeScript-specific: class with implements clause
_CLASS_IMPL_PATTERN_TS = """
    (class_declaration
      name: (type_identifier) @class.name
      (implements_clause (type_identifier) @class.impl)?) @class.def
    """

# Pre-built Query objects for JS (used for .js/.mjs/.cjs files).
# For TS/TSX files we build new Query objects with the appropriate language.
_FUNC_DECL_QUERY_JS = Query(_JS_LANGUAGE, _FUNC_DECL_PATTERN)
_ARROW_QUERY_JS = Query(_JS_LANGUAGE, _ARROW_PATTERN)
_CLASS_QUERY_JS = Query(_JS_LANGUAGE, _CLASS_PATTERN_JS)
_METHOD_QUERY_JS = Query(_JS_LANGUAGE, _METHOD_PATTERN)
_IMPORT_QUERY_JS = Query(_JS_LANGUAGE, _IMPORT_PATTERN)
_REQUIRE_QUERY_JS = Query(_JS_LANGUAGE, _REQUIRE_PATTERN)
_CALL_QUERY_JS = Query(_JS_LANGUAGE, _CALL_PATTERN)
_IFACE_QUERY_TS = Query(_TS_LANGUAGE, _IFACE_PATTERN_TS)
_CLASS_IMPL_QUERY_TS = Query(_TS_LANGUAGE, _CLASS_IMPL_PATTERN_TS)


class JavaScriptParser(LanguageParser):
    """
    Parses JavaScript and TypeScript files using tree-sitter.

    The correct language grammar is selected based on the file extension.
    """

    @property
    def extensions(self) -> tuple[str, ...]:
        return (".js", ".mjs", ".cjs", ".ts", ".tsx")

    @property
    def language_name(self) -> str:
        # The canonical name stored in SymbolNode.language.
        # We return "typescript" or "javascript" in parse() based on extension.
        return "javascript"

    def parse(self, path: Path, source: str, project_root: Path) -> ParseResult:
        result = ParseResult()
        rel_file = self._rel_path(path, project_root)
        source_lines = source.splitlines()
        ext = path.suffix.lower()

        # Pick the right grammar and parser for this file extension.
        is_ts = ext in (".ts", ".tsx")
        if ext == ".tsx":
            lang = _TSX_LANGUAGE
            parser = _TSX_PARSER
        elif ext == ".ts":
            lang = _TS_LANGUAGE
            parser = _TS_PARSER
        else:
            lang = _JS_LANGUAGE
            parser = _JS_PARSER

        lang_name = "typescript" if is_ts else "javascript"

        tree = parser.parse(source.encode("utf-8"))
        root = tree.root_node

        # ------------------------------------------------------------------
        # Classes — build a language-specific Query for the class pattern.
        # For TS/TSX we use the implements-aware pattern; for JS the simple one.
        # We create the Query object fresh here using the correct language so
        # we do NOT need to access a .pattern attribute on a pre-built Query.
        # ------------------------------------------------------------------
        if is_ts:
            class_query = Query(lang, _CLASS_IMPL_PATTERN_TS)
        else:
            class_query = Query(lang, _CLASS_PATTERN_JS)

        cc = QueryCursor(class_query)
        caps = cc.captures(root)

        class_def_nodes = caps.get("class.def", [])
        class_name_nodes = caps.get("class.name", [])
        class_base_nodes = caps.get("class.base", [])
        class_impl_nodes = caps.get("class.impl", [])

        for cls_def in class_def_nodes:
            name_node = next((n for n in class_name_nodes if n.parent == cls_def), None)
            if not name_node or not name_node.text:
                continue
            cls_name = name_node.text.decode("utf-8")
            node_id = self._make_node_id(rel_file, cls_name)
            start_line = cls_def.start_point[0] + 1
            end_line = cls_def.end_point[0] + 1

            node = SymbolNode(
                node_id=node_id,
                kind=SymbolKind.CLASS,
                name=cls_name,
                qualified=f"{rel_file}.{cls_name}",
                file=rel_file,
                line=start_line,
                end_line=end_line,
                language=lang_name,
                docstring=self._extract_jsdoc(cls_def, source_lines),
                signature=self._get_line(source_lines, start_line),
                body=self._extract_body(source_lines, start_line, end_line),
            )
            result.nodes.append(node)

            # INHERITS edges
            for base_node in class_base_nodes:
                if self._is_descendant(base_node, cls_def) and base_node.text:
                    base_name = base_node.text.decode("utf-8")
                    result.edges.append(
                        Edge(
                            source_id=node_id,
                            target_id=self._make_node_id(rel_file, base_name),
                            kind=EdgeKind.INHERITS,
                        )
                    )

            # IMPLEMENTS edges (TypeScript only)
            for impl_node in class_impl_nodes:
                if self._is_descendant(impl_node, cls_def) and impl_node.text:
                    iface_name = impl_node.text.decode("utf-8")
                    result.edges.append(
                        Edge(
                            source_id=node_id,
                            target_id=self._make_node_id(rel_file, iface_name),
                            kind=EdgeKind.IMPLEMENTS,
                        )
                    )

        # ------------------------------------------------------------------
        # Methods inside classes
        # ------------------------------------------------------------------
        method_query = Query(lang, _METHOD_PATTERN)
        mc = QueryCursor(method_query)
        m_caps = mc.captures(root)

        for method_def in m_caps.get("method.def", []):
            name_node = next(
                (n for n in m_caps.get("method.name", []) if n.parent == method_def),
                None,
            )
            if not name_node or not name_node.text:
                continue
            method_name = name_node.text.decode("utf-8")

            # Find the enclosing class name
            parent_cls = self._find_parent_class(method_def)
            if parent_cls is None:
                continue
            cls_name_node = parent_cls.child_by_field_name("name")
            if not cls_name_node or not cls_name_node.text:
                continue
            cls_name = cls_name_node.text.decode("utf-8")

            node_id = self._make_node_id(rel_file, cls_name, method_name)
            cls_node_id = self._make_node_id(rel_file, cls_name)
            start_line = method_def.start_point[0] + 1
            end_line = method_def.end_point[0] + 1

            node = SymbolNode(
                node_id=node_id,
                kind=SymbolKind.METHOD,
                name=method_name,
                qualified=f"{rel_file}.{cls_name}.{method_name}",
                file=rel_file,
                line=start_line,
                end_line=end_line,
                language=lang_name,
                docstring=self._extract_jsdoc(method_def, source_lines),
                signature=self._get_line(source_lines, start_line),
                body=self._extract_body(source_lines, start_line, end_line),
            )
            result.nodes.append(node)
            result.edges.append(
                Edge(source_id=cls_node_id, target_id=node_id, kind=EdgeKind.CONTAINS)
            )

        # ------------------------------------------------------------------
        # Function declarations
        # ------------------------------------------------------------------
        func_query = Query(lang, _FUNC_DECL_PATTERN)
        fc = QueryCursor(func_query)
        f_caps = fc.captures(root)

        for func_def in f_caps.get("func.def", []):
            name_node = next((n for n in f_caps.get("func.name", []) if n.parent == func_def), None)
            if not name_node or not name_node.text:
                continue
            func_name = name_node.text.decode("utf-8")
            if self._find_parent_class(func_def):
                continue  # Already handled as method above
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
                    language=lang_name,
                    docstring=self._extract_jsdoc(func_def, source_lines),
                    signature=self._get_line(source_lines, start_line),
                    body=self._extract_body(source_lines, start_line, end_line),
                )
            )

        # ------------------------------------------------------------------
        # Arrow / expression functions (const foo = () => {})
        # ------------------------------------------------------------------
        arrow_query = Query(lang, _ARROW_PATTERN)
        ac = QueryCursor(arrow_query)
        a_caps = ac.captures(root)

        for i, func_def in enumerate(a_caps.get("func.def", [])):
            name_nodes = a_caps.get("func.name", [])
            name_node = name_nodes[i] if i < len(name_nodes) else None
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
                    language=lang_name,
                    signature=self._get_line(source_lines, start_line),
                    body=self._extract_body(source_lines, start_line, end_line),
                )
            )

        # ------------------------------------------------------------------
        # TypeScript interfaces
        # ------------------------------------------------------------------
        if is_ts:
            iface_query = Query(lang, _IFACE_PATTERN_TS)
            ic = QueryCursor(iface_query)
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
                        language=lang_name,
                        signature=self._get_line(source_lines, start_line),
                        body=self._extract_body(source_lines, start_line, end_line),
                    )
                )

        # ------------------------------------------------------------------
        # Imports
        # ------------------------------------------------------------------
        imp_query = Query(lang, _IMPORT_PATTERN)
        imc = QueryCursor(imp_query)
        for src_node in imc.captures(root).get("import.source", []):
            module_str = src_node.text.decode("utf-8").strip("'\"") if src_node.text else ""
            if module_str:
                result.edges.append(
                    Edge(
                        source_id=rel_file,
                        target_id=self._make_external_id(module_str),
                        kind=EdgeKind.IMPORTS,
                    )
                )

        req_query = Query(lang, _REQUIRE_PATTERN)
        rc = QueryCursor(req_query)
        for src_node in rc.captures(root).get("import.source", []):
            module_str = src_node.text.decode("utf-8").strip("'\"") if src_node.text else ""
            if module_str:
                result.edges.append(
                    Edge(
                        source_id=rel_file,
                        target_id=self._make_external_id(module_str),
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
            if current.type in ("class_declaration", "class"):
                return current
            if current.type in ("function_declaration", "function_expression", "arrow_function"):
                return None
            current = current.parent
        return None

    def _is_descendant(self, node: Node, ancestor: Node) -> bool:
        current = node.parent
        while current:
            if current == ancestor:
                return True
            current = current.parent
        return False

    def _extract_jsdoc(self, node: Node, source_lines: list[str]) -> str:
        """Extract a preceding /** ... */ JSDoc comment if present."""
        start_line = node.start_point[0]  # 0-indexed
        if start_line == 0:
            return ""
        prev_line = source_lines[start_line - 1].strip()
        if prev_line.endswith("*/"):
            # Walk backward to find the opening /**
            comment_lines: list[str] = []
            for i in range(start_line - 1, -1, -1):
                comment_lines.insert(0, source_lines[i].strip())
                if source_lines[i].strip().startswith("/**") or source_lines[i].strip().startswith(
                    "/*"
                ):
                    break
            return "\n".join(comment_lines)
        return ""

    def _get_line(self, source_lines: list[str], line_no: int) -> str:
        if 1 <= line_no <= len(source_lines):
            return source_lines[line_no - 1].strip()
        return ""

    def _extract_body(self, source_lines: list[str], start: int, end: int) -> str:
        if start < 1 or end < start:
            return ""
        return "\n".join(source_lines[start - 1 : end])
