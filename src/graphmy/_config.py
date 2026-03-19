"""
graphmy/_config.py
==================
Configuration dataclass for the graphmy tool.

Users can configure graphmy in three ways (in order of precedence):
  1. Environment variables  (GRAPHMY_OPENAI_API_KEY, etc.)
  2. .graphmy/config.toml   in the project root
  3. Defaults defined here

The GraphmyConfig dataclass is the single source of truth passed throughout
the entire tool — parser, indexer, query engine, and viz all read from it.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# tomllib is in the stdlib from Python 3.11 onward.
# For Python 3.10 we depend on the `tomli` backport (listed in pyproject.toml).
if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib  # type: ignore


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

# Glob patterns always excluded regardless of user config.
# These are universally noisy and never part of the project's own source.
DEFAULT_EXCLUDE: list[str] = [
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/__pycache__/**",
    "**/.git/**",
    "**/dist/**",
    "**/build/**",
    "**/*.min.js",
    "**/*.min.css",
]

# The embedding model used for natural-language queries.
# This model was fine-tuned specifically to map NL queries → code snippets
# (CodeSearchNet dataset) and is the best CPU-friendly option for this task.
DEFAULT_EMBEDDING_MODEL = "flax-sentence-embeddings/st-codesearch-distilroberta-base"

# Default OpenAI model for --explain mode (cheapest capable model).
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

# Maximum source lines inlined per symbol in static HTML output.
# 0 means unlimited — full function bodies are always inlined.
DEFAULT_MAX_BODY_LINES = 0


@dataclass
class GraphmyConfig:
    """
    All configuration for a graphmy run.

    Fields
    ------
    exclude : list[str]
        Glob patterns (relative to project root) to skip during indexing.
        Always merged with DEFAULT_EXCLUDE.
    openai_api_key : str | None
        If set, enables LLM-synthesized answers via --explain and the
        Explain button in --serve mode. Can also be set via
        GRAPHMY_OPENAI_API_KEY environment variable.
    openai_model : str
        OpenAI model name used for synthesis. Defaults to gpt-4o-mini.
    embedding_model : str
        HuggingFace model name for symbol embeddings. Only change this if
        you know what you're doing — the default is fine-tuned for code search.
    max_body_lines : int
        Maximum number of source lines to inline per symbol in static HTML.
        0 = unlimited (full body always). Values > 0 cap each symbol's preview.
    """

    exclude: list[str] = field(default_factory=list)
    openai_api_key: str | None = None
    openai_model: str = DEFAULT_OPENAI_MODEL
    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    max_body_lines: int = DEFAULT_MAX_BODY_LINES

    def __post_init__(self) -> None:
        # Merge user-supplied excludes with the hardcoded defaults.
        # Use a set to deduplicate then convert back to list.
        merged = set(DEFAULT_EXCLUDE) | set(self.exclude)
        self.exclude = list(merged)

        # Environment variables take precedence over config file values.
        env_key = os.environ.get("GRAPHMY_OPENAI_API_KEY")
        if env_key:
            self.openai_api_key = env_key

        env_model = os.environ.get("GRAPHMY_OPENAI_MODEL")
        if env_model:
            self.openai_model = env_model

    @classmethod
    def from_toml(cls, config_path: Path) -> GraphmyConfig:
        """
        Load configuration from a .graphmy/config.toml file.

        The TOML file supports:

            openai_api_key = "sk-..."
            openai_model   = "gpt-4o-mini"
            max_body_lines = 50
            exclude        = ["tests/**", "docs/**"]

        Environment variables are applied on top after loading.
        """
        if not config_path.exists():
            # No config file is fine — use all defaults.
            return cls()

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        return cls(
            exclude=data.get("exclude", []),
            openai_api_key=data.get("openai_api_key"),
            openai_model=data.get("openai_model", DEFAULT_OPENAI_MODEL),
            embedding_model=data.get("embedding_model", DEFAULT_EMBEDDING_MODEL),
            max_body_lines=data.get("max_body_lines", DEFAULT_MAX_BODY_LINES),
        )

    @classmethod
    def load(cls, project_root: Path) -> GraphmyConfig:
        """
        Convenience loader: reads .graphmy/config.toml from the project root
        (if it exists) and applies environment variable overrides.

        This is the entry point used by all CLI commands.
        """
        config_path = project_root / ".graphmy" / "config.toml"
        return cls.from_toml(config_path)

    @property
    def has_openai(self) -> bool:
        """True if an OpenAI API key is configured (enables --explain mode)."""
        return bool(self.openai_api_key)
