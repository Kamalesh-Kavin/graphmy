"""
examples/01_index_and_query.py
==============================
Example: index the graphmy source code itself and run a natural-language query.

Run from the repo root:

    uv run python examples/01_index_and_query.py

What it does:
  1. Indexes graphmy/src/graphmy/ (the package source code)
  2. Runs a structural query: find all symbols named 'parse'
  3. Runs a natural-language query: "find all functions that parse source code"
  4. Prints the top-5 results

This example runs WITHOUT an OpenAI key — it uses pure vector search + graph
expansion. Results are still useful because we use a code-search fine-tuned
embedding model.

Note: the first run will download the embedding model (~329 MB). Subsequent
runs are fast because the model and index are cached in .graphmy/.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Make sure we import the local package, not an installed version.
# This is only needed when running from the repo root without installing.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from graphmy import GraphmyIndex  # noqa: E402


def main() -> None:
    # -----------------------------------------------------------------------
    # 1. Choose the target directory to index.
    #    We use graphmy's own source code as the target — it is always present
    #    and exercises the Python parser fully.
    # -----------------------------------------------------------------------
    project_path = Path(__file__).parent.parent / "src" / "graphmy"

    print(f"[01] Indexing: {project_path}")
    print("     (first run will download the embedding model — ~329 MB)")

    # -----------------------------------------------------------------------
    # 2. Build the index.
    #    GraphmyIndex.build() triggers the full parse + embed pipeline.
    #    The result is cached in .graphmy/ inside project_path's root.
    # -----------------------------------------------------------------------
    index = GraphmyIndex(project_path)
    index.build()

    # -----------------------------------------------------------------------
    # 3. Print index stats so we know what was found.
    # -----------------------------------------------------------------------
    stats = index.stats()
    print("\n[01] Index stats:")
    print(f"     Nodes : {stats['total_nodes']}")
    print(f"     Edges : {stats['total_edges']}")
    print(f"     By kind: {stats['by_kind']}")

    # -----------------------------------------------------------------------
    # 4. Structural query: find all symbols named 'parse'.
    # -----------------------------------------------------------------------
    print("\n[01] Structural query: find_symbol('parse')")
    from graphmy.query._structural import find_symbol

    results = find_symbol(index.graph, "parse")
    print(f"     {results.message}")
    for node in results.nodes[:5]:
        print(f"     → {node.display}")

    # -----------------------------------------------------------------------
    # 5. Natural-language query (no OpenAI key required).
    #    The embedding model maps the NL query into the same vector space as
    #    code signatures + docstrings, so relevant functions float to the top.
    # -----------------------------------------------------------------------
    print("\n[01] NL query: 'find all functions that parse source code'")
    nl_results = index.query("find all functions that parse source code", limit=5)
    print(f"     Found {len(nl_results.hits)} hits:")
    for hit in nl_results.hits:
        tag = "[expansion]" if hit.is_expansion else "[direct]  "
        print(f"     {tag} {hit.node.display}  (distance={hit.distance:.3f})")

    print("\n[01] Done.")


if __name__ == "__main__":
    main()
