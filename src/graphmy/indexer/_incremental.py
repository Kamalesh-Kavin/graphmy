"""
graphmy/indexer/_incremental.py
================================
Incremental indexer — the main entry point for building and updating the graph.

The Indexer class orchestrates:
  1. Walking the project directory, honouring exclude globs
  2. Checking which files have changed (mtime + sha256)
  3. Removing stale nodes from changed/deleted files
  4. Parsing only the changed files via the language registry
  5. Adding new nodes and edges to the GraphStore
  6. Persisting the updated graph and hash state to .graphmy/

After the initial `graphmy index` run, subsequent runs are fast because only
files that have changed since the last run are re-parsed.

File change detection strategy:
  - Primary check: mtime (cheap syscall, catches most changes)
  - Secondary check: sha256 (authoritative, handles mtime-preserving copies,
    git checkouts, rsync, and filesystems with low mtime resolution)
  - A file is considered changed if either mtime OR sha256 differs from stored.
  - Deleted files are detected by walking the stored hash map and checking
    which paths no longer exist on disk.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from graphmy.graph._model import Edge, EdgeKind, SymbolKind, SymbolNode
from graphmy.graph._store import GraphStore
from graphmy.indexer._base import ParseResult
from graphmy.indexer._registry import get_parser
from graphmy._cache import CacheDir, file_mtime, file_sha256
from graphmy._config import GraphmyConfig


class Indexer:
    """
    Builds and incrementally updates the graphmy knowledge graph.

    Parameters
    ----------
    project_root : Path
        The root directory to index. All paths in the graph are stored
        relative to this directory.
    config : GraphmyConfig
        Configuration (exclude globs, embedding model, etc.).
    """

    def __init__(self, project_root: Path, config: GraphmyConfig | None = None) -> None:
        self.project_root = project_root.resolve()
        self.config = config or GraphmyConfig()
        self.cache = CacheDir(self.project_root)

    def build(self, fresh: bool = False) -> GraphStore:
        """
        Build or incrementally update the knowledge graph.

        Parameters
        ----------
        fresh : bool
            If True, ignore the existing cache and re-index everything from scratch.
            If False (default), only re-parse files that have changed.

        Returns
        -------
        GraphStore
            The fully up-to-date knowledge graph.
        """
        # Ensure the .graphmy/ folder exists and .gitignore is updated.
        self.cache.ensure_exists()

        # Load existing graph and hash state (or start fresh).
        if not fresh and self.cache.exists():
            graph = GraphStore.load(self.cache.graph_json, self.project_root)
            file_hashes = self._load_hashes()
        else:
            graph = GraphStore(self.project_root)
            file_hashes = {}

        # Walk the project and collect all indexable source files.
        all_files = self._collect_files()

        # Detect changes: new, modified, and deleted files.
        new_or_changed: list[Path] = []
        deleted: list[str] = []

        current_rel_paths: set[str] = set()
        for file_path in all_files:
            rel = file_path.relative_to(self.project_root).as_posix()
            current_rel_paths.add(rel)
            mtime = file_mtime(file_path)
            sha = file_sha256(file_path)

            stored = file_hashes.get(rel)
            if stored is None:
                # New file — not in index yet.
                new_or_changed.append(file_path)
            else:
                stored_mtime, stored_sha = stored
                if mtime != stored_mtime or sha != stored_sha:
                    # File has changed since last index.
                    new_or_changed.append(file_path)

        # Find deleted files (in hash map but no longer on disk).
        for rel in list(file_hashes.keys()):
            if rel not in current_rel_paths:
                deleted.append(rel)

        # Remove stale data for deleted and changed files.
        for rel in deleted:
            graph.remove_file(rel)
            del file_hashes[rel]

        for file_path in new_or_changed:
            rel = file_path.relative_to(self.project_root).as_posix()
            graph.remove_file(rel)  # remove old data before re-parsing

        # Parse all new/changed files and add to graph.
        parse_errors: list[str] = []
        for file_path in new_or_changed:
            rel = file_path.relative_to(self.project_root).as_posix()
            result = self._parse_file(file_path, rel)
            if result is not None:
                self._integrate(graph, result, rel, file_path)
                parse_errors.extend(result.errors)

            # Update the hash state for this file.
            file_hashes[rel] = [file_mtime(file_path), file_sha256(file_path)]

        # Cross-file call resolution pass:
        # Unresolved call targets (ext::__unresolved__::name) are matched
        # against all known nodes by short name to produce real CALLS edges.
        self._resolve_calls(graph)

        # Persist everything.
        graph.save(self.cache.graph_json)
        self._save_hashes(file_hashes)

        if parse_errors:
            # Print non-fatal parse errors to stderr so they're visible but
            # don't interrupt the index build.
            import sys

            for err in parse_errors:
                print(f"  [parse warning] {err}", file=sys.stderr)

        return graph

    # ------------------------------------------------------------------
    # File collection
    # ------------------------------------------------------------------

    def _collect_files(self) -> list[Path]:
        """
        Walk project_root recursively and return all source files that:
          1. Have a file extension supported by a registered parser
          2. Do not match any exclude glob from the config

        Files are returned in sorted order for deterministic indexing.
        """
        from graphmy.indexer._registry import supported_extensions

        exts = set(supported_extensions())
        result: list[Path] = []

        for file_path in sorted(self.project_root.rglob("*")):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in exts:
                continue
            rel = file_path.relative_to(self.project_root).as_posix()
            if self._is_excluded(rel):
                continue
            result.append(file_path)

        return result

    def _is_excluded(self, rel_path: str) -> bool:
        """
        Return True if rel_path matches any exclude glob pattern.

        Globs are matched against the full relative path (e.g. "tests/foo.py")
        so patterns like "tests/**" work as expected.
        """
        for pattern in self.config.exclude:
            if fnmatch.fnmatch(rel_path, pattern):
                return True
            # Also check just the filename component for simple name patterns.
            if fnmatch.fnmatch(Path(rel_path).name, pattern):
                return True
        return False

    # ------------------------------------------------------------------
    # Parsing a single file
    # ------------------------------------------------------------------

    def _parse_file(self, file_path: Path, rel: str) -> ParseResult | None:
        """
        Parse a single source file using the appropriate language parser.

        Returns None if no parser supports this file type (should not happen
        since _collect_files() already filters by supported extensions).
        """
        parser = get_parser(file_path)
        if parser is None:
            return None

        try:
            source = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            from graphmy.indexer._base import ParseResult as PR

            return PR(errors=[f"Could not read {rel}: {exc}"])

        try:
            return parser.parse(file_path, source, self.project_root)
        except Exception as exc:
            # Tree-sitter can fail on severely malformed files.
            # We record the error but don't crash the whole index build.
            from graphmy.indexer._base import ParseResult as PR

            return PR(errors=[f"Parse error in {rel}: {exc}"])

    # ------------------------------------------------------------------
    # Integrating parse results into the graph
    # ------------------------------------------------------------------

    def _integrate(
        self,
        graph: GraphStore,
        result: ParseResult,
        rel_file: str,
        file_path: Path,
    ) -> None:
        """
        Add all nodes and edges from a ParseResult into the GraphStore.

        Also adds:
          - A FILE node for the source file itself
          - DEFINES edges from the FILE node to all top-level symbols
        """
        # Add the FILE node for this source file.
        file_node = SymbolNode(
            node_id=rel_file,
            kind=SymbolKind.FILE,
            name=file_path.name,
            qualified=rel_file,
            file=rel_file,
            line=0,
            end_line=0,
            language=self._detect_language(file_path),
        )
        graph.add_node(file_node)

        # Add all extracted symbol nodes.
        for node in result.nodes:
            # Apply max_body_lines cap if configured.
            if self.config.max_body_lines > 0:
                node.body = self._cap_body(node.body, self.config.max_body_lines)
            graph.add_node(node)

        # Add DEFINES edges from FILE → top-level symbols.
        # A symbol is "top-level" if it is a FUNCTION or CLASS (not a METHOD,
        # which is already linked via CONTAINS from its class).
        for node in result.nodes:
            if node.kind in (
                SymbolKind.FUNCTION,
                SymbolKind.CLASS,
                SymbolKind.INTERFACE,
                SymbolKind.STRUCT,
                SymbolKind.ENUM,
                SymbolKind.TRAIT,
            ):
                graph.add_edge(
                    Edge(
                        source_id=rel_file,
                        target_id=node.node_id,
                        kind=EdgeKind.DEFINES,
                    )
                )

        # Add all extracted edges.
        for edge in result.edges:
            graph.add_edge(edge)

    # ------------------------------------------------------------------
    # Cross-file call resolution
    # ------------------------------------------------------------------

    def _resolve_calls(self, graph: GraphStore) -> None:
        """
        Resolve unresolved call targets across file boundaries.

        During per-file parsing, calls to functions defined in other files
        are recorded as ext::__unresolved__::<name> stub nodes. This pass
        walks all such edges and replaces the stub target with the actual
        node_id if a matching symbol exists in the graph.

        This is a best-effort pass — calls to genuinely external functions
        (third-party libraries) remain as external stubs.
        """
        g = graph.graph
        unresolved_prefix = "ext::__unresolved__::"

        # Collect all unresolved call edges.
        edges_to_resolve: list[tuple[str, str, Any]] = [
            (src, tgt, key)
            for src, tgt, key, attrs in g.edges(keys=True, data=True)
            if tgt.startswith(unresolved_prefix) and attrs.get("kind") == EdgeKind.CALLS.value
        ]

        # Build a short-name → [node_ids] lookup for resolution.
        name_to_ids: dict[str, list[str]] = {}
        for node in graph.all_nodes():
            if node.kind not in (SymbolKind.FILE, SymbolKind.EXTERNAL):
                name_to_ids.setdefault(node.name, []).append(node.node_id)

        for src, tgt, key in edges_to_resolve:
            short_name = tgt[len(unresolved_prefix) :]
            candidates = name_to_ids.get(short_name, [])

            # Remove the unresolved edge.
            g.remove_edge(src, tgt, key)

            if candidates:
                # Add a resolved CALLS edge to each matching candidate.
                # If there are multiple candidates (same name in different files),
                # we add an edge to all of them — the user can disambiguate in the UI.
                for candidate_id in candidates:
                    if candidate_id != src:  # skip self-calls
                        graph.add_edge(
                            Edge(
                                source_id=src,
                                target_id=candidate_id,
                                kind=EdgeKind.CALLS,
                            )
                        )
            # If no candidates, the edge is silently dropped — it was a call to a
            # genuinely external or built-in symbol not worth showing in the graph.

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _load_hashes(self) -> dict[str, list[Any]]:
        """Load the file hash map from .graphmy/file_hashes.json."""
        if not self.cache.file_hashes_json.exists():
            return {}
        try:
            return json.loads(self.cache.file_hashes_json.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_hashes(self, hashes: dict[str, list[Any]]) -> None:
        """Persist the file hash map to .graphmy/file_hashes.json."""
        self.cache.file_hashes_json.write_text(
            json.dumps(hashes, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _detect_language(self, file_path: Path) -> str:
        """Return the language name for a file based on its extension."""
        parser = get_parser(file_path)
        return parser.language_name if parser else "unknown"

    def _cap_body(self, body: str, max_lines: int) -> str:
        """Cap body text to max_lines lines."""
        if max_lines <= 0:
            return body
        lines = body.splitlines()
        if len(lines) <= max_lines:
            return body
        return "\n".join(lines[:max_lines]) + f"\n# ... ({len(lines) - max_lines} more lines)"
