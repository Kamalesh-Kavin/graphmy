"""
graphmy/__init__.py
====================
Public Python API for graphmy.

This is what Python users interact with when they do ``import graphmy``.
The CLI is a thin layer on top of these classes — all logic lives in the
sub-modules (indexer, graph, search, query, viz).

Quick-start example::

    from pathlib import Path
    import graphmy

    # Index a project
    idx = graphmy.GraphmyIndex(Path("./my-project"))
    idx.build()

    # Natural-language query
    results = idx.query("authentication functions", limit=5)
    for hit in results.hits:
        print(hit.node.name, hit.node.file, hit.node.line)

    # Export to HTML
    idx.viz(output=Path("graph.html"))
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Package version — single source of truth
# ---------------------------------------------------------------------------

__version__ = "0.1.4"

# ---------------------------------------------------------------------------
# Public re-exports from sub-modules
# ---------------------------------------------------------------------------

# Configuration
from graphmy._config import GraphmyConfig  # noqa: F401

# Graph model
from graphmy.graph._model import EdgeKind, SymbolKind, SymbolNode  # noqa: F401
from graphmy.graph._store import GraphStore  # noqa: F401

# ---------------------------------------------------------------------------
# GraphmyIndex — high-level Python API
# ---------------------------------------------------------------------------


class GraphmyIndex:
    """
    High-level Python API for graphmy.

    This class wraps the Indexer, VectorStore, and NLQuery into a single
    easy-to-use object. It is the recommended entry point for programmatic
    use (as opposed to the CLI).

    Parameters
    ----------
    project_root : Path
        The root directory of the project to index. Must exist.
    config : GraphmyConfig | None
        Configuration overrides. If None, the config is loaded from
        ``.graphmy/config.toml`` and environment variables automatically.

    Examples
    --------
    >>> from pathlib import Path
    >>> import graphmy
    >>> idx = graphmy.GraphmyIndex(Path("./my-project"))
    >>> idx.build()
    >>> results = idx.query("find auth functions")
    >>> idx.viz(output=Path("graph.html"))
    """

    def __init__(
        self,
        project_root: Path,
        config: GraphmyConfig | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config = config or GraphmyConfig.load(self.project_root)

        # These are lazily initialised on first use.
        self._graph: GraphStore | None = None

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def build(self, fresh: bool = False) -> GraphmyIndex:
        """
        Build (or incrementally update) the knowledge graph and vector store.

        Parameters
        ----------
        fresh : bool
            If True, ignore the existing cache and re-index from scratch.

        Returns
        -------
        GraphmyIndex
            Returns self so calls can be chained: ``GraphmyIndex(p).build().query(...)``.
        """
        from graphmy._cache import CacheDir
        from graphmy.indexer._incremental import Indexer
        from graphmy.search._embedder import Embedder
        from graphmy.search._vector_store import VectorStore

        indexer = Indexer(self.project_root, self.config)
        self._graph = indexer.build(fresh=fresh)

        cache = CacheDir(self.project_root)
        embedder = Embedder(model_name=self.config.embedding_model)
        vs = VectorStore(vectors_dir=cache.vectors_dir, embedder=embedder)
        vs.upsert(list(self._graph.all_nodes()))

        return self

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def query(
        self,
        query_str: str,
        limit: int = 10,
        explain: bool = False,
    ) -> Any:
        """
        Run a natural-language query over the indexed codebase.

        Parameters
        ----------
        query_str : str
            Natural-language query (e.g. "find authentication functions").
        limit : int
            Maximum number of results.
        explain : bool
            If True and an OpenAI key is configured, synthesize an explanation.

        Returns
        -------
        NLQueryResult
            ``.hits``: ranked list of matching symbols
            ``.explanation``: LLM-synthesized explanation (if explain=True)

        Raises
        ------
        RuntimeError
            If the index has not been built yet (call ``.build()`` first).
        """
        from graphmy._cache import CacheDir
        from graphmy.query._nl import NLQuery
        from graphmy.search._embedder import Embedder
        from graphmy.search._vector_store import VectorStore

        graph = self._get_graph()
        cache = CacheDir(self.project_root)
        embedder = Embedder(model_name=self.config.embedding_model)
        vs = VectorStore(vectors_dir=cache.vectors_dir, embedder=embedder)

        engine = NLQuery(
            graph=graph,
            vector_store=vs,
            embedder=embedder,
            openai_api_key=self.config.openai_api_key,
            openai_model=self.config.openai_model,
        )
        return engine.run(query=query_str, limit=limit, explain=explain)

    # ------------------------------------------------------------------
    # Visualisation
    # ------------------------------------------------------------------

    def viz(
        self,
        output: Path = Path("graph.html"),
        serve: bool = False,
        host: str = "127.0.0.1",
        port: int = 7331,
    ) -> Path:
        """
        Generate a visualisation of the knowledge graph.

        Parameters
        ----------
        output : Path
            Output HTML file path (used in non-serve mode).
        serve : bool
            If True, start a FastAPI server instead of writing a file.
        host : str
            Bind address for serve mode.
        port : int
            Port for serve mode.

        Returns
        -------
        Path
            The path of the written HTML file (non-serve mode only).
        """
        from graphmy._cache import CacheDir
        from graphmy.viz._template import render_html

        graph = self._get_graph()

        if serve:
            from graphmy.search._embedder import Embedder
            from graphmy.search._vector_store import VectorStore
            from graphmy.viz._server import run_server

            cache = CacheDir(self.project_root)
            embedder = Embedder(model_name=self.config.embedding_model)
            vs = VectorStore(vectors_dir=cache.vectors_dir, embedder=embedder)
            run_server(
                graph=graph,
                vector_store=vs,
                project_root=self.project_root,
                config=self.config,
                host=host,
                port=port,
                graphmy_version=__version__,
            )
            # run_server blocks until Ctrl+C, so we never reach here.
            return output  # type: ignore[return-value]

        return render_html(
            graph=graph,
            project_root=self.project_root,
            output_path=output,
            graphmy_version=__version__,
        )

    # ------------------------------------------------------------------
    # Graph access
    # ------------------------------------------------------------------

    @property
    def graph(self) -> GraphStore:
        """Direct access to the knowledge graph (requires build() first)."""
        return self._get_graph()

    def _get_graph(self) -> GraphStore:
        """
        Return the in-memory graph, loading from disk if not yet loaded.
        """
        if self._graph is not None:
            return self._graph

        from graphmy._cache import CacheDir

        cache = CacheDir(self.project_root)
        if cache.exists():
            self._graph = GraphStore.load(cache.graph_json, self.project_root)
            return self._graph

        raise RuntimeError(
            f"No index found at {cache.root}. "
            f"Call GraphmyIndex.build() or run: graphmy index {self.project_root}"
        )


__all__ = [
    "__version__",
    "GraphmyIndex",
    "GraphmyConfig",
    "GraphStore",
    "SymbolNode",
    "SymbolKind",
    "EdgeKind",
]
