"""
graphmy/viz/_exporter.py
=========================
Converts a GraphStore into structured data for the tree-view visualisation.

``export_tree``
    Returns a slim nested tree (folder → file → class → method/function)
    plus a flat detail map and a flat search index.  No source bodies are
    included — the static HTML stays under 2 MB even for large codebases.

Tree node shape (minimal — only what is needed to render a row)
---------------------------------------------------------------
::

    {
        "id":       str,
        "name":     str,
        "kind":     str,   # folder / file / class / function / method / …
        "language": str,
        "file":     str,
        "line":     int,
        "is_async": bool,
        "children": list,  # recursive, same shape
    }

Detail map shape
----------------
``{ node_id → { sig?, doc?, qualified?, end_line?, decorators?,
                children_summary? } }``

Only non-empty fields are included to keep the JSON compact.

``export_cytoscape`` / ``export_cytoscape_subgraph``
    Kept for the FastAPI ``--serve`` mode's graph/subgraph API.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from graphmy.graph._model import SymbolKind
from graphmy.graph._store import GraphStore

# ---------------------------------------------------------------------------
# Tree export (default static viz)
# ---------------------------------------------------------------------------


def export_tree(graph: GraphStore) -> dict[str, Any]:
    """
    Build a hierarchical slim tree from the graph.

    Structure::

        folders (virtual, path-compressed)
          └── files
                └── classes / top-level functions
                      └── methods / nested functions

    Returns
    -------
    dict with keys:

    ``tree``
        List of top-level tree nodes (slim — no bodies, no docstrings).
    ``detail``
        Flat ``{ node_id → detail_dict }`` for the click-detail panel.
        Only non-empty fields are present.
    ``all_nodes``
        Flat ``[{ id, name, kind, file, line }]`` for client-side search.
    ``stats``
        ``{ node_count, edge_count, file_count }``.
    """
    # Build parent→children map from DEFINES and CONTAINS edges only.
    children_of: dict[str, list[str]] = defaultdict(list)
    for src, tgt, attrs in graph.graph.edges(data=True):
        if attrs.get("kind") in ("DEFINES", "CONTAINS"):
            children_of[src].append(tgt)

    # Index all nodes.
    all_nodes_map: dict[str, Any] = {n.node_id: n for n in graph.all_nodes()}

    # File nodes.
    file_nodes = [n for n in all_nodes_map.values() if n.kind.value == SymbolKind.FILE.value]

    # ------------------------------------------------------------------
    # Build slim tree nodes — only what is needed to render a tree row.
    # ------------------------------------------------------------------
    def _build_node(node_id: str) -> dict[str, Any]:
        node = all_nodes_map.get(node_id)
        if node is None:
            return {
                "id": node_id,
                "name": node_id,
                "kind": "unknown",
                "language": "",
                "file": "",
                "line": 0,
                "is_async": False,
                "children": [],
            }

        kind_val = node.kind.value if hasattr(node.kind, "value") else str(node.kind)

        raw_kids = children_of.get(node_id, [])
        kid_nodes = [_build_node(k) for k in raw_kids]
        kid_nodes.sort(key=lambda n: (_KIND_ORDER.get(n["kind"], 99), n["line"]))

        return {
            "id": node.node_id,
            "name": node.name,
            "kind": kind_val,
            "language": node.language or "",
            "file": node.file or "",
            "line": node.line or 0,
            "is_async": bool(node.is_async),
            "children": kid_nodes,
        }

    file_tree_nodes = [_build_node(fn.node_id) for fn in file_nodes]

    # Group files under virtual folder nodes.
    tree = _group_by_folder(file_tree_nodes)

    # ------------------------------------------------------------------
    # Build detail map — richer fields, fetched only on click.
    # Only non-empty fields are stored to keep the JSON compact.
    # ------------------------------------------------------------------
    def _children_summary(node_id: str) -> str:
        """e.g. '3 methods, 1 function'"""
        counts: dict[str, int] = {}
        for kid_id in children_of.get(node_id, []):
            kid = all_nodes_map.get(kid_id)
            if kid:
                kv = kid.kind.value if hasattr(kid.kind, "value") else str(kid.kind)
                counts[kv] = counts.get(kv, 0) + 1
        return ", ".join(f"{v} {k}{'s' if v > 1 else ''}" for k, v in sorted(counts.items()))

    detail: dict[str, dict[str, Any]] = {}
    for node in all_nodes_map.values():
        kind_val = node.kind.value if hasattr(node.kind, "value") else str(node.kind)
        if kind_val == SymbolKind.EXTERNAL.value:
            continue
        entry: dict[str, Any] = {}
        # Static viz: no signatures or docstrings — use --serve for full detail.
        if node.end_line:
            entry["end_line"] = node.end_line
        cs = _children_summary(node.node_id)
        if cs:
            entry["children_summary"] = cs
        if entry:
            detail[node.node_id] = entry

    # Flat search index (no bodies, no docstrings — just enough to search).
    all_flat = [
        {
            "id": n.node_id,
            "name": n.name,
            "kind": n.kind.value if hasattr(n.kind, "value") else str(n.kind),
            "file": n.file or "",
            "line": n.line or 0,
        }
        for n in all_nodes_map.values()
        if (n.kind.value if hasattr(n.kind, "value") else str(n.kind)) != SymbolKind.EXTERNAL.value
    ]

    stats = graph.stats()
    return {
        "tree": tree,
        "detail": detail,
        "all_nodes": all_flat,
        "stats": {
            "node_count": stats["total_nodes"],
            "edge_count": stats["total_edges"],
            "file_count": len(file_nodes),
        },
    }


# Kind display order inside a file node (classes before functions/methods).
_KIND_ORDER = {
    "class": 0,
    "interface": 1,
    "struct": 2,
    "enum": 3,
    "trait": 4,
    "function": 5,
    "method": 6,
}


def _group_by_folder(file_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Group a flat list of file tree nodes into a directory hierarchy.
    Single-child directory chains are path-compressed (collapsed).
    """
    root: dict[str, Any] = {}
    for fn in file_nodes:
        path = fn["file"] or fn["name"]
        parts = PurePosixPath(path).parts
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node.setdefault("__files__", []).append(fn)
    return _trie_to_tree(root, "")


def _trie_to_tree(node: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    """Recursively convert a path-trie to a list of tree items."""
    result: list[dict[str, Any]] = []

    for fn in node.get("__files__", []):
        result.append(fn)

    for name, child in sorted(node.items()):
        if name == "__files__":
            continue
        full_name = name
        # Path compression: collapse single-child-folder chains.
        while True:
            sub_keys = [k for k in child if k != "__files__"]
            if len(sub_keys) == 1 and "__files__" not in child:
                full_name = full_name + "/" + sub_keys[0]
                child = child[sub_keys[0]]
            else:
                break

        children = _trie_to_tree(child, full_name)
        children.sort(key=lambda n: (0 if n["kind"] == "folder" else 1, n["name"]))

        result.append(
            {
                "id": "__folder__/" + full_name,
                "name": full_name,
                "kind": "folder",
                "language": "",
                "file": "",
                "line": 0,
                "is_async": False,
                "children": children,
            }
        )

    result.sort(key=lambda n: (0 if n["kind"] == "folder" else 1, n["name"]))
    return result


# ---------------------------------------------------------------------------
# Flow graph export (static HTML directed graph viz)
# ---------------------------------------------------------------------------


def export_flow_graph(graph: GraphStore) -> dict[str, Any]:
    """
    Build a file-level directed call graph for the static HTML viz.

    Level-1 (overview):
        Nodes  = one node per source file (kind="file").
        Edges  = aggregated CALLS between files.
                 Edge weight = number of individual call sites.

    Level-2 (drill-down, per file):
        Nodes  = all functions/methods defined in that file
                 + any external file nodes they call.
        Edges  = CALLS edges from those functions/methods,
                 annotated with caller and callee names.

    Returns
    -------
    dict with keys:

    ``nodes``
        List of file-node dicts::

            { id, label, language, in_degree, out_degree, symbol_count }

        ``id``           = relative file path (stable, human-readable)
        ``label``        = basename
        ``in_degree``    = how many other files call into this one
        ``out_degree``   = how many other files this one calls into
        ``symbol_count`` = number of functions/methods defined here

    ``edges``
        List of inter-file edge dicts::

            { source, target, weight }

        ``source`` / ``target`` = file path ids
        ``weight``              = number of call sites

    ``subgraphs``
        Dict ``{ file_path → { nodes, edges } }`` for level-2 drill-down.
        Each sub-node: ``{ id, label, kind, line, is_async }``
        Each sub-edge: ``[src_idx, tgt_idx]``  (integer indices into sub.nodes;
        intra-file CALLS only — inter-file calls visible in level-1)

    ``stats``
        ``{ node_count, edge_count, file_count }``
    """
    all_nodes_map: dict[str, Any] = {n.node_id: n for n in graph.all_nodes()}

    # Map node_id → file path (for non-file nodes, use their .file attribute).
    node_file: dict[str, str] = {}
    for n in all_nodes_map.values():
        kv = n.kind.value if hasattr(n.kind, "value") else str(n.kind)
        if kv == SymbolKind.FILE.value:
            node_file[n.node_id] = n.file or n.name
        elif n.file:
            node_file[n.node_id] = n.file

    # ---- Level-1: aggregate CALLS edges to file→file ----------------------
    inter_file_weight: dict[tuple[str, str], int] = defaultdict(int)
    for src, tgt, attrs in graph.graph.edges(data=True):
        if attrs.get("kind") != "CALLS":
            continue
        sf = node_file.get(src)
        tf = node_file.get(tgt)
        if sf and tf and sf != tf:
            inter_file_weight[(sf, tf)] += 1

    # Collect all file paths that appear in at least one edge + standalone files.
    file_nodes_map: dict[str, Any] = {
        n.file or n.name: n
        for n in all_nodes_map.values()
        if (n.kind.value if hasattr(n.kind, "value") else str(n.kind)) == SymbolKind.FILE.value
    }
    all_file_paths: set[str] = set(file_nodes_map.keys())
    for sf, tf in inter_file_weight:
        all_file_paths.add(sf)
        all_file_paths.add(tf)

    # Compute degree stats per file.
    in_deg: dict[str, int] = defaultdict(int)
    out_deg: dict[str, int] = defaultdict(int)
    for (sf, tf), _w in inter_file_weight.items():
        out_deg[sf] += 1
        in_deg[tf] += 1

    # Symbol count per file (functions + methods + classes).
    sym_count: dict[str, int] = defaultdict(int)
    for n in all_nodes_map.values():
        kv = n.kind.value if hasattr(n.kind, "value") else str(n.kind)
        if kv in ("function", "method", "class") and n.file:
            sym_count[n.file] += 1

    # Detect language from file extension.
    _EXT_LANG = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
    }

    def _lang(path: str) -> str:
        fn = all_nodes_map.get(path) or file_nodes_map.get(path)
        if fn and (fn.language if hasattr(fn, "language") else None):
            return fn.language or ""
        ext = PurePosixPath(path).suffix.lower()
        return _EXT_LANG.get(ext, "")

    l1_nodes = [
        {
            "id": fp,
            "label": PurePosixPath(fp).name,
            "language": _lang(fp),
            "in_degree": in_deg[fp],
            "out_degree": out_deg[fp],
            "symbol_count": sym_count[fp],
        }
        for fp in sorted(all_file_paths)
    ]

    l1_edges = [
        {"source": sf, "target": tf, "weight": w}
        for (sf, tf), w in sorted(inter_file_weight.items(), key=lambda x: -x[1])
    ]

    # ---- Level-2: per-file subgraph (function/method nodes + CALLS) --------
    # Symbols defined in each file.
    file_symbols: dict[str, list[Any]] = defaultdict(list)
    for n in all_nodes_map.values():
        kv = n.kind.value if hasattr(n.kind, "value") else str(n.kind)
        if kv in ("function", "method", "class") and n.file:
            file_symbols[n.file].append(n)

    subgraphs: dict[str, dict[str, Any]] = {}
    for fp in all_file_paths:
        syms = file_symbols.get(fp, [])
        if not syms:
            continue

        # Sort symbols by source line for stable ordering.
        syms_sorted = sorted(syms, key=lambda s: s.line or 0)

        # Slim node list — id is kept for hit-testing; label/kind/line for display.
        sub_nodes = [
            {
                "id": s.node_id,
                "label": s.name,
                "kind": s.kind.value if hasattr(s.kind, "value") else str(s.kind),
                "line": s.line or 0,
                "is_async": bool(s.is_async),
            }
            for s in syms_sorted
        ]

        # Build an index-based edge list [src_idx, tgt_idx] for intra-file CALLS
        # only.  Using integer indices (instead of full qualified IDs) keeps the
        # JSON compact — a single large test file can have thousands of call edges
        # and long node IDs, so the savings are significant (≈5× smaller).
        #
        # Inter-file edges are *not* included here — they are already visible in
        # the level-1 overview graph as weighted edges between file nodes.
        node_index: dict[str, int] = {s.node_id: i for i, s in enumerate(syms_sorted)}

        seen_sub: set[tuple[int, int]] = set()
        sub_edges: list[list[int]] = []
        for s in syms_sorted:
            si = node_index[s.node_id]
            for _, tgt, attrs in graph.graph.out_edges(s.node_id, data=True):
                if attrs.get("kind") != "CALLS":
                    continue
                ti = node_index.get(tgt)
                if ti is None or ti == si:
                    # Skip: target not in this file, or self-loop.
                    continue
                key = (si, ti)
                if key not in seen_sub:
                    seen_sub.add(key)
                    sub_edges.append([si, ti])

        if sub_nodes:
            subgraphs[fp] = {"nodes": sub_nodes, "edges": sub_edges}

    stats = graph.stats()
    return {
        "nodes": l1_nodes,
        "edges": l1_edges,
        "subgraphs": subgraphs,
        "stats": {
            "node_count": stats["total_nodes"],
            "edge_count": stats["total_edges"],
            "file_count": len(l1_nodes),
        },
    }


# ---------------------------------------------------------------------------
# Cytoscape export (kept for --serve mode / subgraph API)
# ---------------------------------------------------------------------------


def export_cytoscape(graph: GraphStore) -> dict[str, Any]:
    """
    Convert the full GraphStore into a split cytoscape.js-compatible dict.

    Splits the export into:
    - ``nodes``        : slim cytoscape node elements (render fields only)
    - ``detail``       : { node_id → {docstring, signature, ...} } on-demand
    - ``bodies``       : { node_id → body_string } on-demand
    - ``edges_by_kind``: { kind → [edge elements] } loaded lazily per kind
    - ``stats``        : { node_count, edge_count }
    """
    cy_nodes: list[dict[str, Any]] = []
    detail: dict[str, Any] = {}
    bodies: dict[str, str] = {}

    for node in graph.all_nodes():
        cy_nodes.append(_node_to_cy_slim(node))
        detail[node.node_id] = {
            "end_line": node.end_line,
            "qualified": node.qualified,
            "docstring": node.docstring,
            "signature": node.signature,
            "is_async": node.is_async,
            "decorators": node.decorators,
        }
        if node.body and node.body.strip():
            bodies[node.node_id] = node.body

    edges_by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    edge_id_counter: dict[str, int] = {}

    for src, tgt, attrs in graph.graph.edges(data=True):
        kind = attrs.get("kind", "UNKNOWN")
        base_id = f"{src}--{kind}--{tgt}"
        count = edge_id_counter.get(base_id, 0)
        edge_id_counter[base_id] = count + 1
        edge_id = base_id if count == 0 else f"{base_id}_{count}"
        edges_by_kind[kind].append(
            {"data": {"id": edge_id, "source": src, "target": tgt, "kind": kind}}
        )

    return {
        "nodes": cy_nodes,
        "detail": detail,
        "bodies": bodies,
        "edges_by_kind": dict(edges_by_kind),
        "stats": {
            "node_count": len(cy_nodes),
            "edge_count": sum(len(v) for v in edges_by_kind.values()),
        },
    }


def export_cytoscape_subgraph(
    graph: GraphStore,
    node_ids: list[str],
    hops: int = 1,
) -> dict[str, Any]:
    """
    Export a subgraph centred on the given node_ids.

    Includes seed nodes plus all nodes reachable within ``hops`` steps
    in either direction.  Used by the FastAPI server's search results view.
    """
    g = graph.graph
    frontier: set[str] = set(node_ids)
    visited: set[str] = set(node_ids)

    for _ in range(hops):
        next_frontier: set[str] = set()
        for nid in frontier:
            if g.has_node(nid):
                next_frontier.update(g.predecessors(nid))
                next_frontier.update(g.successors(nid))
        new_nodes = next_frontier - visited
        visited.update(new_nodes)
        frontier = new_nodes

    cy_nodes: list[dict[str, Any]] = []
    detail: dict[str, Any] = {}
    bodies: dict[str, str] = {}

    for nid in visited:
        node = graph.get_node(nid)
        if node:
            cy_nodes.append(_node_to_cy_slim(node))
            detail[node.node_id] = {
                "end_line": node.end_line,
                "qualified": node.qualified,
                "docstring": node.docstring,
                "signature": node.signature,
                "is_async": node.is_async,
                "decorators": node.decorators,
            }
            if node.body and node.body.strip():
                bodies[node.node_id] = node.body

    edges_by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    edge_id_counter: dict[str, int] = {}

    for src, tgt, attrs in g.edges(data=True):
        if src not in visited or tgt not in visited:
            continue
        kind = attrs.get("kind", "UNKNOWN")
        base_id = f"{src}--{kind}--{tgt}"
        count = edge_id_counter.get(base_id, 0)
        edge_id_counter[base_id] = count + 1
        edge_id = base_id if count == 0 else f"{base_id}_{count}"
        edges_by_kind[kind].append(
            {"data": {"id": edge_id, "source": src, "target": tgt, "kind": kind}}
        )

    return {
        "nodes": cy_nodes,
        "detail": detail,
        "bodies": bodies,
        "edges_by_kind": dict(edges_by_kind),
        "stats": {
            "node_count": len(cy_nodes),
            "edge_count": sum(len(v) for v in edges_by_kind.values()),
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _node_to_cy_slim(node: Any) -> dict[str, Any]:
    """Slim cytoscape node — render fields only, no body/detail."""
    kind_val = node.kind.value if hasattr(node.kind, "value") else str(node.kind)
    is_external = kind_val == SymbolKind.EXTERNAL.value
    return {
        "data": {
            "id": node.node_id,
            "label": node.name,
            "kind": kind_val,
            "language": node.language,
            "file": node.file,
            "line": node.line,
        },
        "classes": _node_classes(kind_val, is_external),
    }


def _node_classes(kind: str, is_external: bool) -> str:
    classes = [f"kind-{kind}"]
    if is_external:
        classes.append("external")
    return " ".join(classes)
