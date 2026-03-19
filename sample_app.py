"""
sample_app.py
=============
Self-contained smoke test for graphmy.

This file is a stand-alone demo that:
  1. Creates a tiny synthetic Python project in a temp directory
  2. Indexes it with graphmy
  3. Runs structural queries (callers, subclasses, find_symbol)
  4. Generates a self-contained HTML visualisation
  5. Prints a summary — confirming the full pipeline works end-to-end

Run it with:

    uv run python sample_app.py

No OpenAI key, no internet connection (after first model download), and no
external server are required. The HTML is written to ./sample_graph.html.

Why this file?
  The example scripts (examples/0*.py) require the graphmy source code to be
  present as the target. This file creates its own tiny target from scratch,
  so it is 100% self-contained and can serve as a CI smoke test.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

# Make sure we import the local package, not an installed version.
sys.path.insert(0, str(Path(__file__).parent / "src"))

# ---------------------------------------------------------------------------
# Synthetic Python project — written into a temp directory
# ---------------------------------------------------------------------------
SAMPLE_CODE = '''"""Tiny synthetic codebase used by sample_app.py smoke test."""


class Shape:
    """Abstract base class for all shapes."""

    def area(self) -> float:
        """Return the area of the shape."""
        raise NotImplementedError

    def perimeter(self) -> float:
        """Return the perimeter of the shape."""
        raise NotImplementedError


class Circle(Shape):
    """A circle with a given radius."""

    def __init__(self, radius: float) -> None:
        self.radius = radius

    def area(self) -> float:
        import math
        return math.pi * self.radius ** 2

    def perimeter(self) -> float:
        import math
        return 2 * math.pi * self.radius


class Rectangle(Shape):
    """A rectangle with width and height."""

    def __init__(self, width: float, height: float) -> None:
        self.width = width
        self.height = height

    def area(self) -> float:
        return self.width * self.height

    def perimeter(self) -> float:
        return 2 * (self.width + self.height)


def describe_shape(shape: Shape) -> str:
    """Return a human-readable description of a shape."""
    return (
        f"{shape.__class__.__name__}: "
        f"area={shape.area():.2f}, "
        f"perimeter={shape.perimeter():.2f}"
    )


def main() -> None:
    """Entry point: create shapes and print their descriptions."""
    shapes = [
        Circle(5.0),
        Rectangle(4.0, 6.0),
    ]
    for s in shapes:
        print(describe_shape(s))


if __name__ == "__main__":
    main()
'''


def main() -> None:
    print("=" * 60)
    print("graphmy smoke test — sample_app.py")
    print("=" * 60)

    # -----------------------------------------------------------------------
    # 1. Write the synthetic project to a temp directory.
    # -----------------------------------------------------------------------
    with tempfile.TemporaryDirectory(prefix="graphmy_smoke_") as tmp_dir:
        project = Path(tmp_dir) / "shapes"
        project.mkdir()
        (project / "shapes.py").write_text(SAMPLE_CODE, encoding="utf-8")
        print(f"\n[1] Created synthetic project: {project}")

        # -------------------------------------------------------------------
        # 2. Index the project.
        # -------------------------------------------------------------------
        print("\n[2] Building index...")
        from graphmy import GraphmyIndex

        index = GraphmyIndex(project)
        index.build()

        stats = index.stats()
        print(f"    Nodes : {stats['total_nodes']}")
        print(f"    Edges : {stats['total_edges']}")
        print(f"    By kind : {stats['by_kind']}")

        assert stats["total_nodes"] > 0, "Index should have extracted at least one node"

        # -------------------------------------------------------------------
        # 3. Structural queries.
        # -------------------------------------------------------------------
        from graphmy.query._structural import find_symbol, subclasses

        print("\n[3] Structural query: find_symbol('area')")
        result = find_symbol(index.graph, "area")
        print(f"    {result.message}")
        for n in result.nodes:
            print(f"    → {n.display}")
        assert result.nodes, "Expected to find 'area' method nodes"

        print("\n[3] Structural query: subclasses of Shape")
        # Find the Shape class node_id
        shape_nodes = find_symbol(index.graph, "Shape")
        assert shape_nodes.nodes, "Expected to find Shape class"
        shape_id = shape_nodes.nodes[0].node_id

        subs = subclasses(index.graph, shape_id)
        print(f"    {subs.message}")
        for n in subs.nodes:
            print(f"    → {n.display}")
        # Circle and Rectangle both inherit Shape
        assert len(subs.nodes) >= 2, (
            f"Expected at least 2 subclasses of Shape, got {len(subs.nodes)}"
        )

        # -------------------------------------------------------------------
        # 4. Generate HTML visualisation.
        # -------------------------------------------------------------------
        output_html = Path("sample_graph.html")
        print(f"\n[4] Writing HTML visualisation → {output_html}")
        index.visualize(output_path=output_html)
        assert output_html.exists(), "HTML file should have been created"
        size_kb = output_html.stat().st_size / 1024
        print(f"    File size: {size_kb:.1f} KB")

        print("\n" + "=" * 60)
        print("Smoke test PASSED.")
        print(f"Open {output_html} in a browser to explore the graph.")
        print("=" * 60)


if __name__ == "__main__":
    main()
