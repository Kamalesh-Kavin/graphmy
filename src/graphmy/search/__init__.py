"""
graphmy/search/__init__.py
==========================
Public re-exports for the graphmy search package.

The two classes here are the only things external code should import:
  - ``Embedder``     — lazy-loading sentence-transformers wrapper
  - ``VectorStore``  — chromadb PersistentClient wrapper
"""

from graphmy.search._embedder import EMBEDDING_DIM, Embedder
from graphmy.search._vector_store import VectorStore

__all__ = [
    "Embedder",
    "EMBEDDING_DIM",
    "VectorStore",
]
