"""
graphmy/viz/_template.py
=========================
Jinja2 template loader and HTML file writer for the static viz output.

Responsibilities:
  1. Load the Jinja2 template from the package's ``templates/`` directory
  2. Render it with the graph data and metadata
  3. Write the output HTML file to disk
  4. Warn to stdout (not stderr) if the output file exceeds 50 MB

Usage (from the CLI)::

    from graphmy.viz._template import render_html

    out_path = render_html(
        graph=graph_store,
        project_root=Path("/my/project"),
        output_path=Path("graph.html"),
    )
    print(f"Saved to {out_path}")
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# We import Jinja2 here (not lazily) because this module is always needed
# when `graphmy viz` is run. Jinja2 is a core dependency.
from jinja2 import Environment, FileSystemLoader, select_autoescape

from graphmy.graph._store import GraphStore
from graphmy.viz._exporter import export_cytoscape

# Warn the user (to stdout, so it's visible in pipes) if the output file
# exceeds this size. We do NOT truncate — just warn.
_SIZE_WARN_BYTES = 50 * 1024 * 1024  # 50 MB


def render_html(
    graph: GraphStore,
    project_root: Path,
    output_path: Path,
    graphmy_version: str = "0.1.0",
    serve_mode: bool = False,
) -> Path:
    """
    Render the graph as a self-contained HTML file and write it to disk.

    Parameters
    ----------
    graph : GraphStore
        The knowledge graph to visualise.
    project_root : Path
        Used to derive the project display name (basename of the root dir).
    output_path : Path
        Where to write the HTML file. Parent directories are created if needed.
    graphmy_version : str
        Shown in the HTML footer.
    serve_mode : bool
        If True, the NL query bar is rendered (used by the FastAPI server when
        it serves the page from memory rather than writing to disk).

    Returns
    -------
    Path
        The resolved path of the written HTML file.
    """
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    html = _render_template(
        graph=graph,
        project_root=project_root,
        graphmy_version=graphmy_version,
        serve_mode=serve_mode,
    )

    output_path.write_text(html, encoding="utf-8")

    # Warn if the output is suspiciously large.
    size_bytes = output_path.stat().st_size
    if size_bytes > _SIZE_WARN_BYTES:
        size_mb = size_bytes / (1024 * 1024)
        print(
            f"  [graphmy] Warning: output file is {size_mb:.1f} MB "
            f"(>{_SIZE_WARN_BYTES // (1024 * 1024)} MB). "
            f"Consider using --max-body-lines to reduce size.",
            file=sys.stdout,  # intentionally stdout so it's visible in CI
        )

    return output_path


def render_html_string(
    graph: GraphStore,
    project_root: Path,
    graphmy_version: str = "0.1.0",
    serve_mode: bool = False,
) -> str:
    """
    Render the graph as a self-contained HTML string (without writing to disk).

    Used by the FastAPI server to serve the page directly from memory.

    Parameters
    ----------
    graph : GraphStore
    project_root : Path
    graphmy_version : str
    serve_mode : bool
        Should be True when called from the server (enables the NL query bar).

    Returns
    -------
    str
        Full HTML document as a string.
    """
    return _render_template(
        graph=graph,
        project_root=project_root,
        graphmy_version=graphmy_version,
        serve_mode=serve_mode,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _render_template(
    graph: GraphStore,
    project_root: Path,
    graphmy_version: str,
    serve_mode: bool,
) -> str:
    """
    Load the Jinja2 template and render it with the graph data.

    The graph data is passed as a JSON string embedded inside a
    ``<script type="application/json">`` tag in the template to avoid
    any issues with Jinja2 auto-escaping of curly braces in the JS.
    """
    # Resolve the templates directory — it lives inside the installed package.
    templates_dir = Path(__file__).parent / "templates"

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        # Autoescape HTML but NOT JS — we pass graph data as a JSON blob
        # inside a <script> tag, not inline into HTML attributes.
        autoescape=select_autoescape(["html"]),
        # Preserve newlines and leading whitespace in the template for
        # readability of the rendered output.
        keep_trailing_newline=True,
    )

    template = env.get_template("graph.html.j2")

    # Build the cytoscape.js-compatible graph data.
    cy_data: dict[str, Any] = export_cytoscape(graph)
    stats = graph.stats()

    # Produce a JSON string. We MUST use json.dumps (not |tojson) because
    # body fields may contain arbitrary source code with template-like
    # characters that would confuse Jinja2's auto-escape.
    # The JSON is embedded in a <script type="application/json"> tag so
    # the browser treats it as data, not code.
    graph_data_json = json.dumps(cy_data, ensure_ascii=False)

    return template.render(
        graph_data_json=graph_data_json,
        project_name=project_root.name,
        node_count=stats["total_nodes"],
        edge_count=stats["total_edges"],
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        serve_mode=serve_mode,
        graphmy_version=graphmy_version,
    )
