"""
graphmy/_cli.py
================
Click CLI entry point for the ``graphmy`` command.

Commands:
  graphmy index   <path> [--exclude GLOB] [--fresh]
  graphmy query   <path> <query> [--limit N] [--explain]
  graphmy viz     <path> [--out FILE] [--serve] [--host H] [--port P] [--max-body-lines N]
  graphmy info    <path>
  graphmy config  <path>
  graphmy --version
  graphmy --help

Design philosophy:
  - Every command prints a human-readable summary to stdout.
  - Errors go to stderr (click handles this automatically with sys.exit(1)).
  - Paths are always resolved to absolute paths before use.
  - The CLI is the only place that imports the heavy modules (indexer, search,
    viz) — this keeps `import graphmy` fast for Python API users.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from graphmy import __version__

# ---------------------------------------------------------------------------
# Root command group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version=__version__, prog_name="graphmy")
def cli() -> None:
    """
    graphmy — parse any codebase, build a knowledge graph, visualise it,
    and query it in natural language.

    \b
    Quick start:
      graphmy index   ./my-project
      graphmy query   ./my-project "authentication functions"
      graphmy viz     ./my-project --out graph.html
      graphmy viz     ./my-project --serve
      graphmy info    ./my-project
    """


# ---------------------------------------------------------------------------
# graphmy index
# ---------------------------------------------------------------------------


@cli.command("index")
@click.argument("path", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.option(
    "--exclude",
    "-e",
    multiple=True,
    metavar="GLOB",
    help="Glob pattern to exclude (e.g. 'tests/**'). Repeatable.",
)
@click.option(
    "--fresh",
    "-f",
    is_flag=True,
    default=False,
    help="Ignore the existing cache and re-index everything from scratch.",
)
@click.option(
    "--max-body-lines",
    type=int,
    default=0,
    show_default=True,
    help="Cap source lines inlined per symbol. 0 = unlimited.",
)
def cmd_index(path: str, exclude: tuple[str, ...], fresh: bool, max_body_lines: int) -> None:
    """
    Parse <PATH> and build (or update) the knowledge graph.

    The graph is saved to <PATH>/.graphmy/graph.json. On subsequent runs,
    only files that have changed since the last index are re-parsed.

    Use --fresh to force a full re-index.
    """
    from graphmy._cache import CacheDir
    from graphmy._config import GraphmyConfig
    from graphmy.indexer._incremental import Indexer
    from graphmy.search._embedder import Embedder
    from graphmy.search._vector_store import VectorStore

    project_root = Path(path)

    # Load config (merges .graphmy/config.toml + env vars).
    config = GraphmyConfig.load(project_root)
    if exclude:
        config.exclude = list(config.exclude) + list(exclude)
    if max_body_lines > 0:
        config.max_body_lines = max_body_lines

    click.echo(f"  Indexing {project_root} ...")

    # Build the graph.
    indexer = Indexer(project_root, config)
    graph = indexer.build(fresh=fresh)

    stats = graph.stats()
    click.echo(f"  Graph:  {stats['total_nodes']} nodes, {stats['total_edges']} edges")

    # Build the vector store (embed all symbols for NL queries).
    cache = CacheDir(project_root)
    embedder = Embedder(model_name=config.embedding_model)
    vs = VectorStore(vectors_dir=cache.vectors_dir, embedder=embedder)

    if fresh:
        # On --fresh we need to wipe and re-embed everything.
        import shutil

        if cache.vectors_dir.exists():
            shutil.rmtree(cache.vectors_dir)
        cache.vectors_dir.mkdir(parents=True, exist_ok=True)

    click.echo("  Embedding symbols for NL search (first run may take a minute)...")
    all_nodes = list(graph.all_nodes())
    vs.upsert(all_nodes)
    click.echo(f"  Vector store: {vs.count()} embeddings")

    # Language breakdown.
    if stats.get("by_language"):
        langs = ", ".join(f"{lang}:{count}" for lang, count in sorted(stats["by_language"].items()))
        click.echo(f"  Languages: {langs}")

    click.echo(f"  Done. Cache at: {cache.root}")


# ---------------------------------------------------------------------------
# graphmy query
# ---------------------------------------------------------------------------


@cli.command("query")
@click.argument("path", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.argument("query_str", metavar="QUERY")
@click.option(
    "--limit",
    "-n",
    type=int,
    default=10,
    show_default=True,
    help="Maximum number of results to return.",
)
@click.option(
    "--explain",
    is_flag=True,
    default=False,
    help="Use OpenAI to synthesize a natural-language explanation of the results.",
)
def cmd_query(path: str, query_str: str, limit: int, explain: bool) -> None:
    """
    Search the indexed codebase using natural language.

    QUERY is a natural-language string such as "authentication functions"
    or "database connection pool setup".

    Results are ranked by semantic similarity (vector search) followed by
    graph expansion (callers/callees of the top hits).

    Requires the index to be built first: graphmy index <PATH>
    """
    from graphmy._cache import CacheDir
    from graphmy._config import GraphmyConfig
    from graphmy.graph._store import GraphStore
    from graphmy.query._nl import NLQuery
    from graphmy.search._embedder import Embedder
    from graphmy.search._vector_store import VectorStore

    project_root = Path(path)
    cache = CacheDir(project_root)
    config = GraphmyConfig.load(project_root)

    if not cache.exists():
        click.echo(
            f"  No index found at {cache.root}. Run: graphmy index {path}",
            err=True,
        )
        sys.exit(1)

    click.echo(f"  Loading graph from {cache.graph_json} ...")
    graph = GraphStore.load(cache.graph_json, project_root)

    embedder = Embedder(model_name=config.embedding_model)
    vs = VectorStore(vectors_dir=cache.vectors_dir, embedder=embedder)

    engine = NLQuery(
        graph=graph,
        vector_store=vs,
        embedder=embedder,
        openai_api_key=config.openai_api_key,
        openai_model=config.openai_model,
    )

    click.echo(f'  Searching: "{query_str}" ...')
    result = engine.run(query=query_str, limit=limit, explain=explain)

    if not result.hits:
        click.echo("  No results found.")
        return

    click.echo(f"\n  Results ({len(result.hits)}):\n")
    for i, hit in enumerate(result.hits, 1):
        n = hit.node
        tag = " [expanded]" if hit.is_expansion else f"  dist={hit.distance:.3f}"
        loc = f"{n.file}:{n.line}" if n.line else n.file
        click.echo(f"  {i:2d}. {n.kind.value:10s} {n.name:<30s}  {loc}{tag}")
        if n.signature:
            click.echo(f"       {n.signature}")
        if n.docstring:
            doc_preview = n.docstring[:100].replace("\n", " ")
            click.echo(f'       "{doc_preview}"')
        click.echo()

    if result.explanation:
        click.echo("  Explanation:\n")
        # Wrap long lines for terminal readability.
        import textwrap

        for line in textwrap.wrap(result.explanation, width=80):
            click.echo(f"  {line}")
        click.echo()


# ---------------------------------------------------------------------------
# graphmy viz
# ---------------------------------------------------------------------------


@cli.command("viz")
@click.argument("path", type=click.Path(exists=True, file_okay=False, resolve_path=True))
@click.option(
    "--out",
    "-o",
    default="graph.html",
    show_default=True,
    help="Output HTML file path.",
)
@click.option(
    "--serve",
    "-s",
    is_flag=True,
    default=False,
    help="Start a live server instead of writing a static file.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Bind address for --serve mode.",
)
@click.option(
    "--port",
    "-p",
    type=int,
    default=7331,
    show_default=True,
    help="Port for --serve mode.",
)
@click.option(
    "--max-body-lines",
    type=int,
    default=0,
    show_default=True,
    help="Cap source lines per symbol in output. 0 = unlimited.",
)
def cmd_viz(
    path: str,
    out: str,
    serve: bool,
    host: str,
    port: int,
    max_body_lines: int,
) -> None:
    """
    Visualise the codebase knowledge graph.

    By default, generates a self-contained HTML file (graph.html) that you
    can open in any browser — no server required.

    Use --serve to launch a live server with NL search and optional Explain
    button (requires graphmy[serve] to be installed).

    Requires the index to be built first: graphmy index <PATH>
    """
    from graphmy._cache import CacheDir
    from graphmy._config import GraphmyConfig
    from graphmy.graph._store import GraphStore
    from graphmy.viz._template import render_html

    project_root = Path(path)
    cache = CacheDir(project_root)
    config = GraphmyConfig.load(project_root)

    if max_body_lines > 0:
        config.max_body_lines = max_body_lines

    if not cache.exists():
        click.echo(
            f"  No index found at {cache.root}. Run: graphmy index {path}",
            err=True,
        )
        sys.exit(1)

    click.echo("  Loading graph ...")
    graph = GraphStore.load(cache.graph_json, project_root)
    stats = graph.stats()
    click.echo(f"  Graph: {stats['total_nodes']} nodes, {stats['total_edges']} edges")

    if serve:
        # --serve mode: start FastAPI + uvicorn
        from graphmy.search._embedder import Embedder
        from graphmy.search._vector_store import VectorStore
        from graphmy.viz._server import run_server

        embedder = Embedder(model_name=config.embedding_model)
        vs = VectorStore(vectors_dir=cache.vectors_dir, embedder=embedder)

        run_server(
            graph=graph,
            vector_store=vs,
            project_root=project_root,
            config=config,
            host=host,
            port=port,
            graphmy_version=__version__,
        )
    else:
        # Static HTML mode
        output_path = Path(out).resolve()
        written = render_html(
            graph=graph,
            project_root=project_root,
            output_path=output_path,
            graphmy_version=__version__,
        )
        size_kb = written.stat().st_size / 1024
        click.echo(f"  Saved: {written}  ({size_kb:.0f} KB)")
        click.echo(f"  Open in your browser: file://{written}")


# ---------------------------------------------------------------------------
# graphmy info
# ---------------------------------------------------------------------------


@cli.command("info")
@click.argument("path", type=click.Path(exists=True, file_okay=False, resolve_path=True))
def cmd_info(path: str) -> None:
    """
    Print statistics about the indexed knowledge graph.

    Shows node counts by kind, edge counts by type, language breakdown,
    and cache file sizes.

    Requires the index to be built first: graphmy index <PATH>
    """
    from graphmy._cache import CacheDir
    from graphmy.graph._store import GraphStore

    project_root = Path(path)
    cache = CacheDir(project_root)

    if not cache.exists():
        click.echo(
            f"  No index found at {cache.root}. Run: graphmy index {path}",
            err=True,
        )
        sys.exit(1)

    graph = GraphStore.load(cache.graph_json, project_root)
    stats = graph.stats()

    click.echo(f"\n  Project:  {project_root}")
    click.echo(f"  Cache:    {cache.root}")
    click.echo("\n  Totals:")
    click.echo(f"    Nodes:  {stats['total_nodes']}")
    click.echo(f"    Edges:  {stats['total_edges']}")

    if stats.get("by_kind"):
        click.echo("\n  Nodes by kind:")
        for kind, count in sorted(stats["by_kind"].items(), key=lambda x: -x[1]):
            click.echo(f"    {kind:<12s} {count}")

    if stats.get("by_edge"):
        click.echo("\n  Edges by type:")
        for etype, count in sorted(stats["by_edge"].items(), key=lambda x: -x[1]):
            click.echo(f"    {etype:<14s} {count}")

    if stats.get("by_language"):
        click.echo("\n  Languages:")
        for lang, count in sorted(stats["by_language"].items(), key=lambda x: -x[1]):
            click.echo(f"    {lang:<12s} {count}")

    # Cache file sizes
    click.echo("\n  Cache files:")
    for p in [cache.graph_json, cache.file_hashes_json]:
        if p.exists():
            size_kb = p.stat().st_size / 1024
            click.echo(f"    {p.name:<25s} {size_kb:.0f} KB")
    if cache.vectors_dir.exists():
        vec_size = sum(f.stat().st_size for f in cache.vectors_dir.rglob("*") if f.is_file())
        click.echo(f"    {'vectors/':<25s} {vec_size / 1024:.0f} KB")
    click.echo()


# ---------------------------------------------------------------------------
# graphmy config
# ---------------------------------------------------------------------------


@cli.command("config")
@click.argument("path", type=click.Path(exists=True, file_okay=False, resolve_path=True))
def cmd_config(path: str) -> None:
    """
    Show the current configuration for <PATH>.

    Displays values from .graphmy/config.toml and active environment variable
    overrides. Use this to verify that your API key and model settings are
    correctly picked up.

    The config file is optional — graphmy works with all defaults.
    """
    import os

    from graphmy._cache import CacheDir
    from graphmy._config import GraphmyConfig

    project_root = Path(path)
    config = GraphmyConfig.load(project_root)
    cache = CacheDir(project_root)

    click.echo(f"\n  Config for: {project_root}")
    click.echo(f"  Config file: {cache.config_toml}")
    click.echo(f"  Exists: {'yes' if cache.config_toml.exists() else 'no (using defaults)'}")
    click.echo()
    click.echo(f"  embedding_model   = {config.embedding_model}")
    click.echo(f"  openai_model      = {config.openai_model}")
    click.echo(f"  openai_api_key    = {'*** set ***' if config.has_openai else '(not set)'}")
    click.echo(f"  max_body_lines    = {config.max_body_lines} (0 = unlimited)")
    click.echo(f"  has_openai        = {config.has_openai}")

    # Show active env vars
    env_key = os.environ.get("GRAPHMY_OPENAI_API_KEY")
    env_model = os.environ.get("GRAPHMY_OPENAI_MODEL")
    click.echo()
    click.echo("  Active environment variables:")
    click.echo(f"    GRAPHMY_OPENAI_API_KEY  = {'set' if env_key else 'not set'}")
    click.echo(f"    GRAPHMY_OPENAI_MODEL    = {env_model or '(not set)'}")
    click.echo()

    if not cache.config_toml.exists():
        click.echo("  To create a config file, add .graphmy/config.toml:")
        click.echo('    openai_api_key = "sk-..."')
        click.echo('    openai_model   = "gpt-4o-mini"')
        click.echo("    max_body_lines = 50")
        click.echo()
