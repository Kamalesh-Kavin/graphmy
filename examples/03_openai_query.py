"""
examples/03_openai_query.py
============================
Example: run a natural-language query with OpenAI synthesis (--explain mode).

Run from the repo root:

    GRAPHMY_OPENAI_API_KEY=sk-... uv run python examples/03_openai_query.py

Or set the key in .graphmy/config.toml:

    [openai]
    api_key = "sk-..."

What it does:
  1. Indexes graphmy/src/graphmy/
  2. Runs a NL query WITHOUT explain (shows vector + graph results only)
  3. Runs the same NL query WITH explain (sends top hits to OpenAI GPT for synthesis)

The --explain feature is entirely optional:
  - Without a key, step 3 returns a helpful message explaining how to configure
    the key — it does NOT raise an exception.
  - The query results in step 2 are always fully functional regardless of key.

This demonstrates that graphmy is useful out of the box without any API key,
and that OpenAI synthesis is a progressive enhancement.

Required extras:
    pip install 'graphmy[openai]'
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from graphmy import GraphmyIndex  # noqa: E402


def main() -> None:
    project_path = Path(__file__).parent.parent / "src" / "graphmy"
    openai_key = os.environ.get("GRAPHMY_OPENAI_API_KEY")

    # -----------------------------------------------------------------------
    # 1. Build (or reuse cached) index.
    # -----------------------------------------------------------------------
    print(f"[03] Indexing: {project_path}")
    index = GraphmyIndex(project_path)
    index.build()

    query_text = "how does the incremental indexer avoid re-parsing unchanged files?"

    # -----------------------------------------------------------------------
    # 2. Run WITHOUT explain — fast, no API key needed.
    # -----------------------------------------------------------------------
    print(f"\n[03] Query (no explain): {query_text!r}")
    result_plain = index.query(query_text, limit=5, explain=False)
    print(f"     {len(result_plain.hits)} hits (vector search + graph expansion):")
    for hit in result_plain.hits:
        tag = "[expansion]" if hit.is_expansion else "[direct]  "
        print(f"     {tag} {hit.node.display}  (distance={hit.distance:.3f})")

    # -----------------------------------------------------------------------
    # 3. Run WITH explain — sends top hits to OpenAI GPT-4o-mini for synthesis.
    #    If no API key is set, graphmy returns a graceful message instead of
    #    raising. This shows the feature is truly optional.
    # -----------------------------------------------------------------------
    print(f"\n[03] Query (with explain): {query_text!r}")
    if openai_key:
        print("     OpenAI key found — using GPT synthesis.")
    else:
        print("     No GRAPHMY_OPENAI_API_KEY set — showing graceful fallback message.")

    result_explain = index.query(
        query_text,
        limit=5,
        explain=True,
        openai_api_key=openai_key,
    )

    print(f"\n     Explanation:\n     {result_explain.explanation}")

    print("\n[03] Done.")
    if not openai_key:
        print("\n     Tip: Set GRAPHMY_OPENAI_API_KEY=sk-... to enable LLM synthesis.")


if __name__ == "__main__":
    main()
