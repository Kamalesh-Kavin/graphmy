"""
examples/02_visualize.py
========================
Example: index a project and generate a self-contained HTML graph visualisation.

Run from the repo root:

    uv run python examples/02_visualize.py

What it does:
  1. Indexes graphmy/src/graphmy/ (the package source code)
  2. Exports the graph as a self-contained HTML file (graph.html)
  3. Opens the file in the default browser

The generated HTML:
  - Uses cytoscape.js (loaded from CDN) for interactive graph rendering
  - Has a side panel that shows name, file:line, signature, docstring, and
    source preview when you click a node
  - Includes a natural-language query bar (uses local vector search, no server)
  - Has layout toggles (dagre / circle / breadthfirst)
  - Is 100% self-contained — no server needed, works offline after generation

To also start a live FastAPI server with a real-time NL query bar:

    graphmy viz src/graphmy --serve

(requires `pip install graphmy[serve]`)
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from graphmy import GraphmyIndex  # noqa: E402


def main() -> None:
    # -----------------------------------------------------------------------
    # 1. Index the graphmy source code.
    # -----------------------------------------------------------------------
    project_path = Path(__file__).parent.parent / "src" / "graphmy"
    output_file = Path(__file__).parent.parent / "graph.html"

    print(f"[02] Indexing: {project_path}")

    index = GraphmyIndex(project_path)
    index.build()

    stats = index.stats()
    print(f"[02] Index complete — {stats['total_nodes']} nodes, {stats['total_edges']} edges")

    # -----------------------------------------------------------------------
    # 2. Export the graph to a self-contained HTML file.
    # -----------------------------------------------------------------------
    print(f"[02] Writing HTML visualisation → {output_file}")
    index.visualize(output_path=output_file)

    # Check the file size and warn if it is large.
    size_mb = output_file.stat().st_size / (1024 * 1024)
    if size_mb > 10:
        print(
            f"     Note: file is {size_mb:.1f} MB. Consider using --max-body-lines to reduce size."
        )
    else:
        print(f"     File size: {size_mb:.2f} MB")

    # -----------------------------------------------------------------------
    # 3. Open in the default browser.
    # -----------------------------------------------------------------------
    print("[02] Opening in browser...")
    webbrowser.open(output_file.as_uri())

    print(f"[02] Done. Open {output_file} in any browser to explore.")


if __name__ == "__main__":
    main()
