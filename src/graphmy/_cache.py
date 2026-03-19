"""
graphmy/_cache.py
=================
Manages the .graphmy/ index cache folder inside a project root.

The cache folder holds three things:
  - graph.json        : the full networkx node-link graph (nodes + edges)
  - file_hashes.json  : per-file {path: [mtime, sha256]} for incremental re-index
  - vectors/          : chromadb PersistentClient folder (SQLite + HNSW index)
  - config.toml       : optional user config (created by `graphmy config`)

On first index, graphmy also adds .graphmy/ to the project's .gitignore so
the cache is never accidentally committed to version control.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


# The name of the cache folder created inside every indexed project.
CACHE_DIR_NAME = ".graphmy"


class CacheDir:
    """
    Thin wrapper around the .graphmy/ folder.

    All path resolution for cache files goes through here — nothing else in
    the codebase should hardcode ".graphmy/" directly.

    Parameters
    ----------
    project_root : Path
        The root directory of the project being indexed.
    """

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.root = self.project_root / CACHE_DIR_NAME

    # ------------------------------------------------------------------
    # Well-known paths inside .graphmy/
    # ------------------------------------------------------------------

    @property
    def graph_json(self) -> Path:
        """Path to the serialised networkx graph (node-link JSON)."""
        return self.root / "graph.json"

    @property
    def file_hashes_json(self) -> Path:
        """Path to the per-file mtime+sha256 hash map used for incremental index."""
        return self.root / "file_hashes.json"

    @property
    def vectors_dir(self) -> Path:
        """Path to the chromadb PersistentClient folder."""
        return self.root / "vectors"

    @property
    def config_toml(self) -> Path:
        """Path to the optional user config file."""
        return self.root / "config.toml"

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def ensure_exists(self) -> None:
        """
        Create the .graphmy/ folder (and vectors/ subfolder) if they don't
        exist yet, and add .graphmy/ to the project .gitignore.
        """
        self.root.mkdir(parents=True, exist_ok=True)
        self.vectors_dir.mkdir(parents=True, exist_ok=True)
        self._update_gitignore()

    def exists(self) -> bool:
        """True if the cache folder and graph.json both exist."""
        return self.root.exists() and self.graph_json.exists()

    def _update_gitignore(self) -> None:
        """
        Append '.graphmy/' to <project_root>/.gitignore if not already present.

        We never overwrite or reorder existing .gitignore entries — we only
        append if the entry is missing. This is safe to call on every index run.
        """
        gitignore_path = self.project_root / ".gitignore"
        entry = ".graphmy/"

        if gitignore_path.exists():
            existing = gitignore_path.read_text(encoding="utf-8")
            # Check for the exact entry on its own line to avoid false matches
            # (e.g. matching ".graphmy/vectors" when we only want ".graphmy/").
            lines = [line.strip() for line in existing.splitlines()]
            if entry in lines:
                return
            # Append with a leading newline to avoid running onto an existing line.
            with open(gitignore_path, "a", encoding="utf-8") as f:
                if not existing.endswith("\n"):
                    f.write("\n")
                f.write(f"{entry}\n")
        else:
            # No .gitignore yet — create one with just our entry.
            gitignore_path.write_text(f"{entry}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# File hashing utilities (used by incremental indexer)
# ---------------------------------------------------------------------------


def file_sha256(path: Path) -> str:
    """
    Compute the SHA-256 hex digest of a file's contents.

    Used alongside mtime for incremental change detection:
    - mtime check is O(1) (syscall) and catches most changes
    - sha256 is the authoritative check for cases where mtime is unreliable
      (e.g. git checkout, rsync, FAT filesystems)
    """
    h = hashlib.sha256()
    # Read in 64 KB chunks to avoid loading huge files entirely into memory.
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def file_mtime(path: Path) -> float:
    """Return the last-modified timestamp of a file as a float (seconds since epoch)."""
    return path.stat().st_mtime
