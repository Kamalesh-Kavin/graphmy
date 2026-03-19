"""
graphmy/indexer/_rust.py
========================
Rust language parser using tree-sitter.

Extracts from .rs files:
  - Standalone functions (fn foo() {})
  - Struct, enum, and union type definitions
  - Trait definitions
  - Impl blocks (both inherent impls `impl Foo {}` and trait impls `impl Trait for Foo {}`)
  - Functions inside impl blocks (methods)
  - Use declarations (import edges)
  - Call sites (best-effort)

Key Rust-specific concepts:
  - `impl Trait for Type` → IMPLEMENTS edge from Type → Trait
  - `impl Type` → CONTAINS edges from Type → its methods
  - Traits may have default method implementations (fn with body inside trait_item)
  - No inheritance in Rust — only trait implementation
"""

from __future__ import annotations

from pathlib import Path

import tree_sitter_rust as tsrust
from tree_sitter import Language, Node, Parser, Query, QueryCursor

from graphmy.graph._model import Edge, EdgeKind, SymbolKind, SymbolNode
from graphmy.indexer._base import LanguageParser, ParseResult

_RUST_LANGUAGE = Language(tsrust.language())
_PARSER = Parser(_RUST_LANGUAGE)

_FUNC_QUERY = Query(
    _RUST_LANGUAGE,
    """
    (function_item name: (identifier) @func.name) @func.def
    """,
)

_STRUCT_QUERY = Query(
    _RUST_LANGUAGE,
    """
    (struct_item name: (type_identifier) @struct.name) @struct.def
    (enum_item name: (type_identifier) @enum.name) @enum.def
    (union_item name: (type_identifier) @union.name) @union.def
    """,
)

_TRAIT_QUERY = Query(
    _RUST_LANGUAGE,
    """
    (trait_item name: (type_identifier) @trait.name) @trait.def
    """,
)

# Impl blocks — two forms:
#   impl Foo { }              → type field only
#   impl Trait for Foo { }    → trait + type fields
_IMPL_QUERY = Query(
    _RUST_LANGUAGE,
    """
    (impl_item
      type: (type_identifier) @impl.type) @impl.def
    """,
)

_USE_QUERY = Query(
    _RUST_LANGUAGE,
    """
    (use_declaration argument: (_) @use.path)
    """,
)


class RustParser(LanguageParser):
    """Parses Rust source files using tree-sitter."""

    @property
    def extensions(self) -> tuple[str, ...]:
        return (".rs",)

    @property
    def language_name(self) -> str:
        return "rust"

    def parse(self, path: Path, source: str, project_root: Path) -> ParseResult:
        result = ParseResult()
        rel_file = self._rel_path(path, project_root)
        source_lines = source.splitlines()

        tree = _PARSER.parse(source.encode("utf-8"))
        root = tree.root_node

        # ------------------------------------------------------------------
        # Structs, enums, unions
        # ------------------------------------------------------------------
        sc = QueryCursor(_STRUCT_QUERY)
        s_caps = sc.captures(root)

        for cap_name, kind in [
            ("struct.name", SymbolKind.STRUCT),
            ("enum.name", SymbolKind.ENUM),
            ("union.name", SymbolKind.STRUCT),
        ]:
            cap_name.replace(".name", ".def")
            for name_node in s_caps.get(cap_name, []):
                if not name_node.text:
                    continue
                type_name = name_node.text.decode("utf-8")
                # Find the parent def node
                parent_def = name_node.parent
                if parent_def is None:
                    continue
                node_id = self._make_node_id(rel_file, type_name)
                start_line = parent_def.start_point[0] + 1
                end_line = parent_def.end_point[0] + 1

                result.nodes.append(
                    SymbolNode(
                        node_id=node_id,
                        kind=kind,
                        name=type_name,
                        qualified=f"{rel_file}::{type_name}",
                        file=rel_file,
                        line=start_line,
                        end_line=end_line,
                        language=self.language_name,
                        docstring=self._extract_rust_doc(parent_def, source_lines),
                        signature=self._get_line(source_lines, start_line),
                        body=self._extract_body(source_lines, start_line, end_line),
                    )
                )

        # ------------------------------------------------------------------
        # Traits
        # ------------------------------------------------------------------
        tc = QueryCursor(_TRAIT_QUERY)
        t_caps = tc.captures(root)

        for name_node in t_caps.get("trait.name", []):
            if not name_node.text:
                continue
            trait_name = name_node.text.decode("utf-8")
            parent_def = name_node.parent
            if parent_def is None:
                continue
            node_id = self._make_node_id(rel_file, trait_name)
            start_line = parent_def.start_point[0] + 1
            end_line = parent_def.end_point[0] + 1

            result.nodes.append(
                SymbolNode(
                    node_id=node_id,
                    kind=SymbolKind.TRAIT,
                    name=trait_name,
                    qualified=f"{rel_file}::{trait_name}",
                    file=rel_file,
                    line=start_line,
                    end_line=end_line,
                    language=self.language_name,
                    docstring=self._extract_rust_doc(parent_def, source_lines),
                    signature=self._get_line(source_lines, start_line),
                    body=self._extract_body(source_lines, start_line, end_line),
                )
            )

        # ------------------------------------------------------------------
        # Impl blocks — extract methods + IMPLEMENTS edges
        # ------------------------------------------------------------------
        ic = QueryCursor(_IMPL_QUERY)
        i_caps = ic.captures(root)

        for impl_def in i_caps.get("impl.def", []):
            # Get the concrete type being implemented
            type_node = next(
                (n for n in i_caps.get("impl.type", []) if n.parent == impl_def),
                None,
            )
            if not type_node or not type_node.text:
                continue
            impl_type = type_node.text.decode("utf-8")
            type_node_id = self._make_node_id(rel_file, impl_type)

            # Check if this is a trait impl: `impl Trait for Type`
            trait_node = impl_def.child_by_field_name("trait")
            if trait_node and trait_node.text:
                trait_name = trait_node.text.decode("utf-8")
                trait_node_id = self._make_node_id(rel_file, trait_name)
                result.edges.append(
                    Edge(
                        source_id=type_node_id,
                        target_id=trait_node_id,
                        kind=EdgeKind.IMPLEMENTS,
                    )
                )

            # Extract methods inside this impl block
            body_node = impl_def.child_by_field_name("body")
            if body_node is None:
                continue

            for child in body_node.children:
                if child.type == "function_item":
                    fn_name_node = child.child_by_field_name("name")
                    if not fn_name_node or not fn_name_node.text:
                        continue
                    fn_name = fn_name_node.text.decode("utf-8")
                    method_node_id = self._make_node_id(rel_file, impl_type, fn_name)
                    start_line = child.start_point[0] + 1
                    end_line = child.end_point[0] + 1

                    result.nodes.append(
                        SymbolNode(
                            node_id=method_node_id,
                            kind=SymbolKind.METHOD,
                            name=fn_name,
                            qualified=f"{rel_file}::{impl_type}::{fn_name}",
                            file=rel_file,
                            line=start_line,
                            end_line=end_line,
                            language=self.language_name,
                            docstring=self._extract_rust_doc(child, source_lines),
                            signature=self._get_line(source_lines, start_line),
                            body=self._extract_body(source_lines, start_line, end_line),
                        )
                    )
                    result.edges.append(
                        Edge(
                            source_id=type_node_id,
                            target_id=method_node_id,
                            kind=EdgeKind.CONTAINS,
                        )
                    )

        # ------------------------------------------------------------------
        # Top-level functions (not inside impl blocks)
        # ------------------------------------------------------------------
        fc = QueryCursor(_FUNC_QUERY)
        f_caps = fc.captures(root)

        for func_def in f_caps.get("func.def", []):
            # Skip if inside an impl block
            if self._find_parent_impl(func_def):
                continue
            func_name_node: Node | None = next(
                (n for n in f_caps.get("func.name", []) if n.parent == func_def), None
            )
            if not func_name_node or not func_name_node.text:
                continue
            func_name = func_name_node.text.decode("utf-8")
            node_id = self._make_node_id(rel_file, func_name)
            start_line = func_def.start_point[0] + 1
            end_line = func_def.end_point[0] + 1

            result.nodes.append(
                SymbolNode(
                    node_id=node_id,
                    kind=SymbolKind.FUNCTION,
                    name=func_name,
                    qualified=f"{rel_file}::{func_name}",
                    file=rel_file,
                    line=start_line,
                    end_line=end_line,
                    language=self.language_name,
                    docstring=self._extract_rust_doc(func_def, source_lines),
                    signature=self._get_line(source_lines, start_line),
                    body=self._extract_body(source_lines, start_line, end_line),
                )
            )

        # ------------------------------------------------------------------
        # Use declarations (imports)
        # ------------------------------------------------------------------
        uc = QueryCursor(_USE_QUERY)
        u_caps = uc.captures(root)
        for use_node in u_caps.get("use.path", []):
            path_str = use_node.text.decode("utf-8") if use_node.text else ""
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

    def _find_parent_impl(self, node: Node) -> Node | None:
        current = node.parent
        while current:
            if current.type == "impl_item":
                return current
            current = current.parent
        return None

    def _extract_rust_doc(self, node: Node, source_lines: list[str]) -> str:
        """Extract /// doc comments directly preceding a Rust item."""
        start_line = node.start_point[0]
        doc_lines: list[str] = []
        for i in range(start_line - 1, -1, -1):
            stripped = source_lines[i].strip()
            if stripped.startswith("///"):
                doc_lines.insert(0, stripped.lstrip("/ ").strip())
            elif stripped.startswith("//!"):
                doc_lines.insert(0, stripped.lstrip("/! ").strip())
            else:
                break
        return " ".join(doc_lines)

    def _get_line(self, source_lines: list[str], line_no: int) -> str:
        if 1 <= line_no <= len(source_lines):
            return source_lines[line_no - 1].strip()
        return ""

    def _extract_body(self, source_lines: list[str], start: int, end: int) -> str:
        if start < 1 or end < start:
            return ""
        return "\n".join(source_lines[start - 1 : end])
