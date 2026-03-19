"""
graphmy/query/__init__.py
==========================
Public re-exports for the graphmy query package.

Two sub-modules:
  - ``_structural`` — deterministic graph-traversal queries (no model required)
  - ``_nl``         — natural-language queries via vector search + graph expansion
"""

from graphmy.query._nl import NLHit, NLQuery, NLQueryResult
from graphmy.query._structural import (
    StructuralResult,
    call_chain,
    callees,
    callers,
    find_symbol,
    implementors,
    imports_of,
    subclasses,
    superclasses,
)

__all__ = [
    # NL query
    "NLQuery",
    "NLQueryResult",
    "NLHit",
    # Structural queries
    "StructuralResult",
    "callers",
    "callees",
    "subclasses",
    "superclasses",
    "implementors",
    "call_chain",
    "imports_of",
    "find_symbol",
]
