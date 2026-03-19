"""
graphmy/viz/_template.py
=========================
Jinja2 template loader and HTML file writer for the static viz output.

The static viz is a **two-level directed call graph**:

- Level 1 (default): one node per source file, edges = aggregated CALLS
  between files.  Node size ∝ in-degree (how often the file is called into).
- Level 2 (drill-down): click a file node to see its functions/methods and
  their intra-file call edges, plus arrows to the files they call out to.

Rendered on an HTML ``<canvas>`` using D3 force simulation — no SVG, no CDN.

Data passed to the template
----------------------------
``flow_json``
    JSON string of the full flow graph data::

        {
          nodes:     [{ id, label, language, in_degree, out_degree, symbol_count }],
          edges:     [{ source, target, weight }],
          subgraphs: { file_path: { nodes: [...], edges: [...] } },
        }

``project_name``, ``node_count``, ``edge_count``, ``file_count``
    Toolbar metadata.

``graphmy_version``
    Shown in the page footer.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from graphmy.graph._store import GraphStore
from graphmy.viz._exporter import export_flow_graph


def render_html(
    graph: GraphStore,
    project_root: Path,
    output_path: Path,
    graphmy_version: str = "0.1.0",
) -> Path:
    """
    Render the graph as a self-contained HTML file and write it to disk.

    Parameters
    ----------
    graph : GraphStore
    project_root : Path
        Used for the project display name (basename).
    output_path : Path
        Destination file.  Parent directories are created if needed.
    graphmy_version : str

    Returns
    -------
    Path
        Resolved path of the written file.
    """
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html = _render_template(graph, project_root, graphmy_version)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def render_html_string(
    graph: GraphStore,
    project_root: Path,
    graphmy_version: str = "0.1.0",
) -> str:
    """
    Render the graph as an HTML string without writing to disk.
    Used by the FastAPI server (serve mode).
    """
    return _render_template(graph, project_root, graphmy_version)


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------


def _render_template(
    graph: GraphStore,
    project_root: Path,
    graphmy_version: str,
) -> str:
    templates_dir = Path(__file__).parent / "templates"

    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html"]),
        keep_trailing_newline=True,
    )

    template = env.get_template("graph.html.j2")
    data = export_flow_graph(graph)

    flow_payload = {
        "nodes": data["nodes"],
        "edges": data["edges"],
        "subgraphs": data["subgraphs"],
    }

    return template.render(
        flow_json=json.dumps(flow_payload, ensure_ascii=False),
        project_name=project_root.name,
        node_count=data["stats"]["node_count"],
        edge_count=data["stats"]["edge_count"],
        file_count=data["stats"]["file_count"],
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        graphmy_version=graphmy_version,
    )
