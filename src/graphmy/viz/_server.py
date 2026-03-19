"""
graphmy/viz/_server.py
=======================
FastAPI server for ``graphmy viz --serve`` mode.

The server provides:
  GET  /                    — serves the self-contained HTML viz (NL query bar enabled)
  GET  /api/query           — NL semantic search: returns JSON hits + optional LLM explanation
  GET  /api/node/{node_id}  — full detail for a single node (signature, docstring, body)
  GET  /api/graph           — full cytoscape.js graph data as JSON (raw graph export)
  GET  /api/stats           — graph statistics (node/edge counts by kind/language)

The NL query bar in the HTML calls /api/query to highlight matching nodes in the
graph and display result cards with signature, docstring, source, and callers/callees.
Clicking "Jump to" on a card calls showLevel2() + highlight in the canvas renderer.

This module is only imported when ``graphmy[serve]`` is installed.
If FastAPI or uvicorn are not installed, a clear error is raised.

Architecture note:
  The server is initialised once with the graph and vector store. All state is
  module-level (within the ``create_app()`` factory) so the server is stateless
  per-request — no global singletons that would make testing awkward.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from graphmy._config import GraphmyConfig
from graphmy.graph._store import GraphStore
from graphmy.search._embedder import Embedder
from graphmy.search._vector_store import VectorStore
from graphmy.viz._exporter import export_cytoscape
from graphmy.viz._template import render_html_string


def create_app(
    graph: GraphStore,
    vector_store: VectorStore,
    project_root: Path,
    config: GraphmyConfig,
    graphmy_version: str = "0.1.0",
) -> Any:
    """
    Create and return the FastAPI application.

    Parameters
    ----------
    graph : GraphStore
        The indexed knowledge graph.
    vector_store : VectorStore
        The chromadb vector store for NL query.
    project_root : Path
        Used to derive the project display name.
    config : GraphmyConfig
        User configuration (OpenAI key, model, etc.).
    graphmy_version : str
        Shown in the page title and stats endpoint.

    Returns
    -------
    FastAPI application instance.
    """
    try:
        from fastapi import FastAPI, Query
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError as exc:
        raise ImportError(
            "FastAPI is required for --serve mode. Install with: pip install 'graphmy[serve]'"
        ) from exc

    from graphmy.query._nl import NLQuery

    # Shared embedder — model is loaded lazily on first NL query.
    embedder = Embedder(model_name=config.embedding_model)

    # NL query engine — wraps graph + vector store + optional OpenAI.
    nl_engine = NLQuery(
        graph=graph,
        vector_store=vector_store,
        embedder=embedder,
        openai_api_key=config.openai_api_key,
        openai_model=config.openai_model,
    )

    # Pre-render the HTML once at startup (graph data is static between requests).
    # Pass nl_enabled=True so the template shows the NL query bar.
    html_content = render_html_string(
        graph=graph,
        project_root=project_root,
        graphmy_version=graphmy_version,
        nl_enabled=True,
    )

    # Create the FastAPI app.
    app = FastAPI(
        title="graphmy",
        description="Codebase knowledge graph",
        version=graphmy_version,
        docs_url="/api/docs",
        redoc_url=None,
    )

    # ----------------------------------------------------------------
    # Routes
    # ----------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        """
        Serve the self-contained graph visualisation HTML page.

        The HTML includes the NL query bar that calls /api/query to highlight
        matching nodes and display result cards.
        """
        return HTMLResponse(content=html_content)

    @app.get("/api/graph")
    async def api_graph() -> JSONResponse:
        """
        Return the full cytoscape.js-format graph data as JSON.

        Clients can use this to re-render the graph with custom tools
        or to fetch the raw node/edge data programmatically.
        """
        data = export_cytoscape(graph)
        return JSONResponse(content=data)

    @app.get("/api/stats")
    async def api_stats() -> JSONResponse:
        """
        Return graph statistics: node counts by kind, edge counts by type,
        language breakdown.
        """
        stats = graph.stats()
        stats["version"] = graphmy_version
        stats["project"] = project_root.name
        return JSONResponse(content=stats)

    @app.get("/api/query")
    async def api_query(
        q: str = Query(..., description="Natural-language query string"),
        limit: int = Query(10, ge=1, le=50, description="Maximum results"),
        explain: bool = Query(False, description="Generate LLM explanation"),
    ) -> JSONResponse:
        """
        Run a natural-language query and return matching symbols.

        Response shape::

            {
              "query": "...",
              "hits": [
                {
                  "node": { node_id, kind, name, qualified, file, line, end_line,
                             language, docstring, signature, is_async, decorators },
                  "distance": 0.12,
                  "is_expansion": false,
                  "callers": [ ...slim node dicts... ],
                  "callees": [ ...slim node dicts... ]
                }
              ],
              "explanation": "..."   // empty unless explain=true + OpenAI key configured
            }

        The viz JS calls this endpoint, renders result cards, and highlights the
        returned node_ids on the canvas.
        """
        result = nl_engine.run(query=q, limit=limit, explain=explain)
        return JSONResponse(content=result.as_dict())

    @app.get("/api/node/{node_id:path}")
    async def api_node(node_id: str) -> JSONResponse:
        """
        Return full detail for a single symbol node, including its source body.

        The ``node_id:path`` converter allows slashes in node IDs
        (e.g. ``api/auth.py::validate_token``).

        Response shape::

            {
              "node_id":    "api/auth.py::validate_token",
              "kind":       "function",
              "name":       "validate_token",
              "qualified":  "auth.validate_token",
              "file":       "api/auth.py",
              "line":       42,
              "end_line":   61,
              "language":   "python",
              "signature":  "def validate_token(token: str) -> bool:",
              "docstring":  "Validate a JWT token ...",
              "body":       "def validate_token(...): ...",
              "is_async":   false,
              "decorators": [],
              "callers": [ ...slim node dicts (name, file, line, kind)... ],
              "callees": [ ...slim node dicts... ]
            }

        Returns 404 if the node_id is not found in the graph.
        """
        from fastapi import HTTPException

        node = graph.get_node(node_id)
        if node is None:
            raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")

        # Build slim caller/callee lists (no body — just enough for context links).
        def _slim(n: Any) -> dict[str, Any]:
            return {
                "node_id": n.node_id,
                "name": n.name,
                "kind": n.kind.value if hasattr(n.kind, "value") else str(n.kind),
                "file": n.file or "",
                "line": n.line or 0,
                "language": n.language or "",
            }

        callers = [_slim(c) for c in graph.callers(node_id)]
        callees = [_slim(c) for c in graph.callees(node_id)]

        return JSONResponse(
            content={
                "node_id": node.node_id,
                "kind": node.kind.value if hasattr(node.kind, "value") else str(node.kind),
                "name": node.name,
                "qualified": node.qualified or "",
                "file": node.file or "",
                "line": node.line or 0,
                "end_line": node.end_line or 0,
                "language": node.language or "",
                "signature": node.signature or "",
                "docstring": node.docstring or "",
                "body": node.body or "",
                "is_async": bool(node.is_async),
                "decorators": list(node.decorators or []),
                "callers": callers,
                "callees": callees,
            }
        )

    return app


def run_server(
    graph: GraphStore,
    vector_store: VectorStore,
    project_root: Path,
    config: GraphmyConfig,
    host: str = "127.0.0.1",
    port: int = 7331,
    graphmy_version: str = "0.1.0",
) -> None:
    """
    Create and run the FastAPI server with uvicorn.

    This is the entry point called by ``graphmy viz --serve``.

    Parameters
    ----------
    graph : GraphStore
    vector_store : VectorStore
    project_root : Path
    config : GraphmyConfig
    host : str
        Bind address. Default: 127.0.0.1 (localhost only).
    port : int
        Port number. Default: 7331.
    graphmy_version : str
    """
    try:
        import uvicorn
    except ImportError as exc:
        raise ImportError(
            "uvicorn is required for --serve mode. Install with: pip install 'graphmy[serve]'"
        ) from exc

    app = create_app(
        graph=graph,
        vector_store=vector_store,
        project_root=project_root,
        config=config,
        graphmy_version=graphmy_version,
    )

    print(f"  [graphmy] Serving graph at http://{host}:{port}")
    print("  [graphmy] Press Ctrl+C to stop.")

    uvicorn.run(
        app,
        host=host,
        port=port,
        # Single worker is correct here — the graph is loaded once in memory
        # and shared across requests. Multiple workers would each load their
        # own copy, wasting RAM.
        workers=1,
        # Suppress uvicorn's default "Started server process" banner so our
        # message above is the last thing the user sees before the browser.
        log_level="warning",
    )
