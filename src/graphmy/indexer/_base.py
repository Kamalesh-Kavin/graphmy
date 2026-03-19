"""
graphmy/indexer/_base.py
========================
Abstract base class for all language parsers.

Every language parser (Python, JS, Go, etc.) implements LanguageParser.
The indexer calls parser.parse(path, source) and receives a ParseResult
containing all extracted nodes and edges for that file.

This design means:
  - Adding a new language = adding one new file that implements LanguageParser
  - The indexer loop is language-agnostic
  - Tests can mock or stub individual parsers without touching the indexer
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from graphmy.graph._model import Edge, SymbolNode


@dataclass
class ParseResult:
    """
    The output of parsing a single source file.

    Fields
    ------
    nodes : list[SymbolNode]
        All symbols extracted from the file (functions, classes, methods, …).
        Does NOT include the FILE node itself — the indexer adds that.
    edges : list[Edge]
        All relationships extracted from the file (CALLS, IMPORTS, CONTAINS, …).
        DEFINES edges from the FILE node are also added by the indexer.
    errors : list[str]
        Non-fatal parse warnings (e.g. "could not resolve call target foo").
        The file is still partially indexed even if errors is non-empty.
    """

    nodes: list[SymbolNode] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class LanguageParser(ABC):
    """
    Abstract base class for a language-specific tree-sitter parser.

    Subclasses must implement:
      - `extensions`   — the file extensions this parser handles
      - `language_name` — the canonical language string stored in SymbolNode.language
      - `parse`        — extract all nodes and edges from a source file

    The base class provides `_make_node_id()` and `_make_file_node_id()` helpers
    so all parsers produce consistent node IDs without duplicating the logic.
    """

    @property
    @abstractmethod
    def extensions(self) -> tuple[str, ...]:
        """
        File extensions handled by this parser, e.g. ('.py',) or ('.js', '.mjs').
        Leading dots are required.
        """
        ...

    @property
    @abstractmethod
    def language_name(self) -> str:
        """
        Canonical language identifier stored in SymbolNode.language.
        Must be one of: python, javascript, typescript, go, rust, java.
        """
        ...

    @abstractmethod
    def parse(self, path: Path, source: str, project_root: Path) -> ParseResult:
        """
        Parse a single source file and return all extracted symbols and edges.

        Parameters
        ----------
        path : Path
            Absolute path to the source file.
        source : str
            Full source text of the file (already read by the indexer).
        project_root : Path
            Absolute path to the project root. Used to compute relative paths
            for SymbolNode.file (so node IDs are portable across machines).

        Returns
        -------
        ParseResult
            Extracted nodes, edges, and any non-fatal errors.
        """
        ...

    # ------------------------------------------------------------------
    # Shared helpers — all parsers use these for consistent node IDs
    # ------------------------------------------------------------------

    def _rel_path(self, path: Path, project_root: Path) -> str:
        """
        Return the path relative to project_root as a forward-slash string.

        e.g. /home/user/myproject/src/auth.py → "src/auth.py"
        """
        try:
            return path.relative_to(project_root).as_posix()
        except ValueError:
            # Path is outside project root — use absolute path as fallback.
            return path.as_posix()

    def _make_node_id(self, rel_file: str, *parts: str) -> str:
        """
        Build a stable node_id from a file path and symbol name components.

        Format: "<rel_file>::<part1>::<part2>..."
        Examples:
          src/auth.py::validate_token
          src/auth.py::AuthService::validate_token
        """
        return "::".join([rel_file, *parts])

    def _make_external_id(self, module: str, symbol: str = "") -> str:
        """
        Build a node_id for an external (out-of-project) symbol stub.

        Format: "ext::<module>" or "ext::<module>::<symbol>"
        """
        parts = ["ext", module]
        if symbol:
            parts.append(symbol)
        return "::".join(parts)

    def _cap_body(self, body: str, max_lines: int) -> str:
        """
        Cap body text to max_lines lines (0 = unlimited).
        Used when --max-body-lines is set by the user.
        """
        if max_lines <= 0:
            return body
        lines = body.splitlines()
        if len(lines) <= max_lines:
            return body
        return "\n".join(lines[:max_lines]) + f"\n# ... ({len(lines) - max_lines} more lines)"
