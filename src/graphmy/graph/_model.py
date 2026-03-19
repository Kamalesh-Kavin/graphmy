"""
graphmy/graph/_model.py
=======================
Data model for all nodes and edges in the graphmy knowledge graph.

Every parsed symbol (function, class, method, file, etc.) becomes a
SymbolNode. Relationships between symbols become typed Edge objects.

Design principles:
- Dataclasses only — no ORM, no DB, no magic.
- All fields have explicit types so the rest of the codebase can rely on them.
- SymbolNode is serialisable to/from a plain dict so it can be stored in
  networkx node attributes and round-tripped through JSON without loss.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Symbol kinds
# ---------------------------------------------------------------------------


class SymbolKind(str, Enum):
    """
    The kind of a symbol node in the graph.

    FILE     — a source file (the root of a DEFINES/CONTAINS tree)
    CLASS    — a class definition
    FUNCTION — a top-level function or standalone def
    METHOD   — a function defined inside a class body
    INTERFACE— an interface or protocol definition (TS, Java, Go)
    STRUCT   — a struct definition (Go, Rust)
    ENUM     — an enum definition (Rust, Java, TS)
    TRAIT    — a Rust trait definition
    EXTERNAL — a stub node representing a symbol outside the project root
               (e.g. an imported function from a third-party library)
    """

    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    INTERFACE = "interface"
    STRUCT = "struct"
    ENUM = "enum"
    TRAIT = "trait"
    EXTERNAL = "external"


# ---------------------------------------------------------------------------
# Edge types
# ---------------------------------------------------------------------------


class EdgeKind(str, Enum):
    """
    The type of a directed edge between two symbol nodes.

    CALLS      — function/method A calls function/method B
    IMPORTS    — file A imports from file B (or an external module)
    DEFINES    — file A defines symbol B (top-level)
    CONTAINS   — class A contains method B
    INHERITS   — class A inherits from class B (extends)
    IMPLEMENTS — class A implements interface B
    """

    CALLS = "CALLS"
    IMPORTS = "IMPORTS"
    DEFINES = "DEFINES"
    CONTAINS = "CONTAINS"
    INHERITS = "INHERITS"
    IMPLEMENTS = "IMPLEMENTS"


# ---------------------------------------------------------------------------
# SymbolNode
# ---------------------------------------------------------------------------


@dataclass
class SymbolNode:
    """
    A single node in the graphmy knowledge graph.

    Every parsed symbol — file, class, function, method, external stub — is
    represented as a SymbolNode. Nodes are stored in networkx as plain dicts
    (via asdict()) so they survive JSON serialisation without custom encoders.

    Fields
    ------
    node_id : str
        Stable unique identifier. Format:
          file::       "src/auth.py"
          function::   "src/auth.py::validate_token"
          method::     "src/auth.py::AuthService::validate_token"
          external::   "ext::requests::get"
        Colons are used as separators because they are illegal in file paths
        on all supported platforms, so they can't collide with path components.

    kind : SymbolKind
        One of FILE, CLASS, FUNCTION, METHOD, INTERFACE, STRUCT, ENUM,
        TRAIT, EXTERNAL.

    name : str
        Short display name (e.g. "validate_token").

    qualified : str
        Fully-qualified dotted path (e.g. "auth.AuthService.validate_token").

    file : str
        Relative path from project root to the source file (e.g. "src/auth.py").
        Empty string for EXTERNAL nodes.

    line : int
        1-indexed line number of the definition. 0 for FILE and EXTERNAL nodes.

    end_line : int
        1-indexed last line of the definition body. 0 if unknown.

    language : str
        Source language: "python", "javascript", "typescript", "go", "rust", "java".
        Empty for EXTERNAL nodes.

    docstring : str
        First docstring or leading comment extracted from the symbol body.
        Empty string if none found.

    signature : str
        The function/method signature as a single-line string.
        For classes/files, this is the class header line.
        Empty for EXTERNAL nodes.

    body : str
        Full source lines of the symbol body. Used for the source preview
        in the viz detail panel. May be capped by --max-body-lines.

    is_async : bool
        True for async functions/methods. Relevant for Python and JS/TS.

    decorators : list[str]
        List of decorator names for Python functions/classes.
    """

    node_id: str
    kind: SymbolKind
    name: str
    qualified: str
    file: str
    line: int
    end_line: int
    language: str
    docstring: str = ""
    signature: str = ""
    body: str = ""
    is_async: bool = False
    decorators: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """
        Convert to a plain dict suitable for networkx node attributes or JSON.

        SymbolKind is stored as its string value so JSON round-trips cleanly.
        """
        d = asdict(self)
        d["kind"] = self.kind.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SymbolNode":
        """
        Reconstruct a SymbolNode from the dict produced by to_dict().

        The 'kind' field is coerced back to a SymbolKind enum.
        """
        d = dict(d)  # copy to avoid mutating caller's dict
        d["kind"] = SymbolKind(d["kind"])
        return cls(**d)

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_external(self) -> bool:
        """True if this is a stub node for a symbol outside the project."""
        return self.kind == SymbolKind.EXTERNAL

    @property
    def display(self) -> str:
        """
        Short human-readable label used in CLI output and viz tooltips.
        Format: "<kind> <name>  (<file>:<line>)"
        """
        loc = f"{self.file}:{self.line}" if self.line else self.file
        return f"{self.kind.value} {self.name}  ({loc})"


# ---------------------------------------------------------------------------
# Edge
# ---------------------------------------------------------------------------


@dataclass
class Edge:
    """
    A directed edge between two SymbolNodes.

    Stored in networkx as edge attributes (source → target with a 'kind' key).

    Fields
    ------
    source_id : str
        node_id of the source SymbolNode.
    target_id : str
        node_id of the target SymbolNode.
    kind : EdgeKind
        The relationship type (CALLS, IMPORTS, etc.).
    """

    source_id: str
    target_id: str
    kind: EdgeKind

    def to_dict(self) -> dict[str, str]:
        """Minimal dict for networkx edge attribute storage."""
        return {"kind": self.kind.value}
