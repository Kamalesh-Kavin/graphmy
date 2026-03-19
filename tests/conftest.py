"""
tests/conftest.py
=================
Shared pytest fixtures for graphmy tests.

Fixtures:
  sample_python_dir   — Path to tests/fixtures/sample_python/
  sample_go_dir       — Path to tests/fixtures/sample_go/
  sample_js_dir       — Path to tests/fixtures/sample_js/
  sample_rust_dir     — Path to tests/fixtures/sample_rust/
  sample_java_dir     — Path to tests/fixtures/sample_java/
  tmp_project         — A temporary directory with sample_python/ contents
                        plus a fresh GraphStore (used by integration tests)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

# Location of all fixture files
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def sample_python_dir() -> Path:
    """Path to the sample Python fixture directory."""
    return FIXTURES_DIR / "sample_python"


@pytest.fixture()
def sample_go_dir() -> Path:
    """Path to the sample Go fixture directory."""
    return FIXTURES_DIR / "sample_go"


@pytest.fixture()
def sample_js_dir() -> Path:
    """Path to the sample JS fixture directory."""
    return FIXTURES_DIR / "sample_js"


@pytest.fixture()
def sample_rust_dir() -> Path:
    """Path to the sample Rust fixture directory."""
    return FIXTURES_DIR / "sample_rust"


@pytest.fixture()
def sample_java_dir() -> Path:
    """Path to the sample Java fixture directory."""
    return FIXTURES_DIR / "sample_java"


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """
    A temporary project directory seeded with the sample Python fixture files.

    The .graphmy/ cache does NOT exist at fixture creation time — tests that
    need the index must call Indexer(tmp_project).build() themselves.
    This ensures each test starts with a clean state.
    """
    src = FIXTURES_DIR / "sample_python"
    dst = tmp_path / "project"
    shutil.copytree(src, dst)
    return dst


@pytest.fixture()
def empty_graph():
    """An empty GraphStore (no project root needed for unit tests)."""
    from graphmy.graph._store import GraphStore

    return GraphStore()
