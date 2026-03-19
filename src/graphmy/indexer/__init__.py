"""
graphmy/indexer/__init__.py
============================
Public re-exports for the graphmy indexer package.

The main entry point for users and CLI commands is the ``Indexer`` class.
Language parsers and the registry are available for advanced use (e.g.
implementing a custom language parser or running a single-file parse).
"""

from graphmy.indexer._base import LanguageParser, ParseResult
from graphmy.indexer._incremental import Indexer
from graphmy.indexer._registry import get_parser, supported_extensions, supported_languages

__all__ = [
    "Indexer",
    "LanguageParser",
    "ParseResult",
    "get_parser",
    "supported_extensions",
    "supported_languages",
]
