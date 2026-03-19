"""
graphmy/search/_vector_store.py
================================
ChromaDB PersistentClient wrapper for symbol embeddings.

Each indexed symbol (function, class, method, …) has its name + signature +
docstring concatenated into a "document" string, embedded by the Embedder,
and stored here. Natural-language queries are embedded with the same model
and the nearest neighbours are returned.

Why ChromaDB?
  - Embedded: runs in the same process, no server required
  - Persistent: saved to .graphmy/vectors/ and reloaded on next run
  - Fast: HNSW approximate nearest-neighbour index, sub-millisecond queries
  - No API keys: entirely local, privacy-preserving

Collection schema:
  id        → node_id (str)    — unique, matches GraphStore node key
  embedding → list[float]      — 768-d vector from Embedder
  document  → str              — human-readable text used for embedding
  metadata  → dict             — node_id (redundant for easy retrieval)

The collection is created once and reused across runs. On incremental re-index,
we delete and re-insert documents for changed files (identified by node_id prefix).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from graphmy.search._embedder import Embedder


class VectorStore:
    """
    Chromadb-backed vector store for symbol embeddings.

    Parameters
    ----------
    vectors_dir : Path
        Directory where chromadb stores its SQLite database and HNSW index.
        Should be ``CacheDir.vectors_dir`` (i.e. ``.graphmy/vectors/``).
    embedder : Embedder | None
        The embedding model wrapper. If None, a default Embedder is created.
        Supply your own to share a single model instance across the process.

    Usage
    -----
    >>> store = VectorStore(vectors_dir=Path(".graphmy/vectors"))
    >>> store.upsert(nodes)           # index a list of SymbolNodes
    >>> results = store.query("authentication logic", n_results=10)
    """

    # Name of the Chroma collection that holds all symbol embeddings.
    _COLLECTION_NAME = "graphmy_symbols"

    def __init__(self, vectors_dir: Path, embedder: Embedder | None = None) -> None:
        self.vectors_dir = vectors_dir
        self.embedder = embedder or Embedder()

        # Lazily initialised — not created until first use so CLI startup
        # does not import chromadb (which is heavyweight).
        self._client: Any = None
        self._collection: Any = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upsert(self, nodes: list[Any]) -> None:
        """
        Add or update embeddings for a list of SymbolNodes.

        Each node's "document" string is:
            "<name>  <signature>  <docstring>"
        The three fields are concatenated with two spaces so the model
        can use them together for semantic matching.

        Parameters
        ----------
        nodes : list[SymbolNode]
            The nodes to embed and store. External and FILE nodes are skipped
            because they don't have meaningful text content to embed.
        """
        from graphmy.graph._model import SymbolKind

        # Filter out FILE and EXTERNAL nodes — nothing to embed.
        indexable = [n for n in nodes if n.kind not in (SymbolKind.FILE, SymbolKind.EXTERNAL)]
        if not indexable:
            return

        # Build document strings for embedding.
        documents = [self._make_document(n) for n in indexable]
        ids = [n.node_id for n in indexable]
        metadatas = [{"node_id": n.node_id} for n in indexable]

        # Embed in batch (much faster than one-at-a-time).
        embeddings = self.embedder.embed_many(documents)

        # Upsert into chromadb (insert if new, update if already present).
        collection = self._get_collection()
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    def query(self, text: str, n_results: int = 10) -> list[dict[str, Any]]:
        """
        Find the ``n_results`` most semantically similar symbols.

        Parameters
        ----------
        text : str
            Natural-language query (e.g. "authenticate user with JWT token").
        n_results : int
            Maximum number of results to return.

        Returns
        -------
        list[dict]
            Each dict contains:
              - ``node_id``   : the matching symbol's node_id
              - ``distance``  : L2 distance (lower = more similar)
              - ``document``  : the text that was embedded
        """
        collection = self._get_collection()

        # Don't request more results than the collection has.
        count = collection.count()
        if count == 0:
            return []
        k = min(n_results, count)

        query_embedding = self.embedder.embed(text)

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=k,
            include=["metadatas", "documents", "distances"],
        )

        # chromadb returns lists-of-lists (one inner list per query).
        # We only issued one query, so take index 0.
        ids = results["ids"][0]
        distances = results["distances"][0]
        documents = results["documents"][0]

        return [
            {"node_id": nid, "distance": dist, "document": doc}
            for nid, dist, doc in zip(ids, distances, documents, strict=False)
        ]

    def delete_by_file(self, file_path: str) -> None:
        """
        Remove all embeddings whose node_id starts with ``file_path::``.

        Called by the incremental indexer before re-parsing a changed file.

        Parameters
        ----------
        file_path : str
            Relative path from project root (e.g. "src/auth.py").
        """
        collection = self._get_collection()

        # chromadb's ``where`` filter uses exact equality.
        # We use a ``get`` + bulk delete pattern to handle the prefix match
        # because chromadb does not have a native ``startswith`` filter.
        prefix = f"{file_path}::"
        existing = collection.get(
            where={"node_id": {"$gte": prefix}},
            include=[],
        )

        # The $gte trick doesn't give us a true prefix filter — do it in Python.
        ids_to_delete = [nid for nid in existing["ids"] if nid.startswith(prefix)]
        if ids_to_delete:
            collection.delete(ids=ids_to_delete)

    def count(self) -> int:
        """Return the total number of embeddings stored."""
        return self._get_collection().count()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_collection(self) -> Any:
        """Return (or lazily create) the chromadb collection."""
        if self._collection is None:
            self._collection = self._open_collection()
        return self._collection

    def _open_collection(self) -> Any:
        """
        Open the chromadb PersistentClient and get/create the collection.

        Uses ``get_or_create_collection`` so the collection persists across
        process restarts. The HNSW index is stored in ``vectors_dir``.
        """
        try:
            import chromadb
        except ImportError as exc:
            raise ImportError(
                "chromadb is required for natural-language queries. "
                "Install graphmy normally — it is a core dependency."
            ) from exc

        self.vectors_dir.mkdir(parents=True, exist_ok=True)

        # PersistentClient persists data automatically — no .persist() needed.
        client = chromadb.PersistentClient(path=str(self.vectors_dir))

        collection = client.get_or_create_collection(
            name=self._COLLECTION_NAME,
            # Use cosine similarity (better for semantic search than L2).
            metadata={"hnsw:space": "cosine"},
        )

        self._client = client
        return collection

    @staticmethod
    def _make_document(node: Any) -> str:
        """
        Build the text string that is embedded and stored for a symbol.

        We concatenate name, signature, and docstring so the model can use
        all three for semantic matching. The order matters slightly — name
        and signature come first because they are the most discriminative.

        Example output:
            "validate_token  def validate_token(token: str) -> bool  Validate a JWT token."
        """
        parts = [node.name]
        if node.signature:
            parts.append(node.signature)
        if node.docstring:
            parts.append(node.docstring)
        return "  ".join(parts)
