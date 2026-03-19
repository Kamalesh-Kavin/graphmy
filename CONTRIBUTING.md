# Contributing to graphmy

Thank you for your interest in contributing! This guide covers how to set up your development environment, run tests, and submit changes.

---

## Development setup

`graphmy` uses [uv](https://github.com/astral-sh/uv) as its package manager.

```bash
# Clone the repo
git clone https://github.com/Kamalesh-Kavin/graphmy.git
cd graphmy

# Install all dependencies including dev extras
export PATH="$HOME/.local/bin:$PATH"
uv sync --extra serve --extra openai --extra dev

# Run the test suite
uv run pytest tests/ -v

# Run the linter
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/

# Run type checking
uv run mypy src/graphmy
```

---

## Project structure

```
src/graphmy/
├── indexer/      # tree-sitter parsers, one module per language
├── graph/        # networkx graph model and persistence
├── search/       # sentence-transformers + chromadb vector index
├── query/        # structural (graph traversal) + NL query engine
├── viz/          # cytoscape.js HTML exporter + FastAPI server
├── _cli.py       # click CLI entry point
├── _config.py    # GraphmyConfig dataclass
└── _cache.py     # .graphmy/ folder management
```

Every file must be **heavily commented** — if you cannot explain the code without the AI, you haven't learned it yet. This is a non-negotiable project philosophy.

---

## Adding a new language

1. Create `src/graphmy/indexer/_<language>.py` implementing `LanguageParser`
2. Add the grammar package to `pyproject.toml` dependencies
3. Register the extension mapping in `src/graphmy/indexer/_registry.py`
4. Add a fixture in `tests/fixtures/sample_<language>/`
5. Add `tests/test_indexer_<language>.py`

---

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Kotlin parser
fix: handle empty Go files gracefully
docs: update CLI reference in README
chore: bump tree-sitter to 0.25.3
```

---

## Submitting a PR

1. Fork the repo and create a branch: `git checkout -b feat/my-feature`
2. Make your changes with tests and documentation
3. Run `uv run pytest` and `uv run ruff check src/ tests/` — both must pass
4. Open a PR against `main` using the PR template

---

## Reporting bugs

Use the [bug report template](.github/ISSUE_TEMPLATE/bug_report.md).

Please include:
- `graphmy --version` output
- The command you ran
- Full error output
- OS and Python version
