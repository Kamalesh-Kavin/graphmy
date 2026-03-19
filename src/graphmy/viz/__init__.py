"""
graphmy/viz/__init__.py
========================
Public re-exports for the graphmy viz package.

The key entry points:
  - ``render_html``        — write the self-contained HTML to a file
  - ``render_html_string`` — render the HTML to a string (used by --serve)
  - ``run_server``         — launch the FastAPI + uvicorn server
  - ``export_cytoscape``   — convert a GraphStore to cytoscape.js JSON
"""

from graphmy.viz._exporter import export_cytoscape, export_cytoscape_subgraph
from graphmy.viz._template import render_html, render_html_string

__all__ = [
    "render_html",
    "render_html_string",
    "export_cytoscape",
    "export_cytoscape_subgraph",
]

# run_server is imported lazily (only available with graphmy[serve]) so
# we don't list it in __all__ to avoid ImportError at import time.
