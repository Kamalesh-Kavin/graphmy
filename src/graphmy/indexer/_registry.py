"""
graphmy/indexer/_registry.py
=============================
Language parser registry — maps file extensions to parser instances.

The registry is the single place that knows which parser handles which files.
Adding a new language means:
  1. Writing a new LanguageParser subclass
  2. Registering it here with its file extensions

All parsers are instantiated once and reused — they are stateless.
"""

from __future__ import annotations

from pathlib import Path

from graphmy.indexer._base import LanguageParser
from graphmy.indexer._go import GoParser
from graphmy.indexer._java import JavaParser
from graphmy.indexer._javascript import JavaScriptParser
from graphmy.indexer._python import PythonParser
from graphmy.indexer._rust import RustParser

# ---------------------------------------------------------------------------
# Instantiate all parsers once.
# Parser objects are stateless — all parsing state lives in local variables
# inside the parse() method, so sharing a single instance across threads is safe.
# ---------------------------------------------------------------------------
_PARSERS: list[LanguageParser] = [
    PythonParser(),
    JavaScriptParser(),
    GoParser(),
    RustParser(),
    JavaParser(),
]

# Build extension → parser mapping for O(1) lookup.
_EXT_TO_PARSER: dict[str, LanguageParser] = {}
for _p in _PARSERS:
    for _ext in _p.extensions:
        _EXT_TO_PARSER[_ext] = _p


def get_parser(path: Path) -> LanguageParser | None:
    """
    Return the appropriate LanguageParser for a file, or None if unsupported.

    Lookup is based on the file's lowercase suffix (e.g. '.py', '.ts').

    Parameters
    ----------
    path : Path
        Path to the source file (only the suffix is used).

    Returns
    -------
    LanguageParser | None
        The parser instance for this file type, or None if no parser exists.
    """
    return _EXT_TO_PARSER.get(path.suffix.lower())


def supported_extensions() -> list[str]:
    """Return the list of all file extensions graphmy can parse."""
    return sorted(_EXT_TO_PARSER.keys())


def supported_languages() -> list[str]:
    """Return the list of canonical language names graphmy supports."""
    return sorted({p.language_name for p in _PARSERS})
