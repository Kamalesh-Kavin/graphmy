# Changelog

All notable changes to `graphmy` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

## [0.1.0] - 2025-01-01

### Added
- Initial release
- Multi-language parsing: Python, JavaScript, TypeScript, Go, Rust, Java (tree-sitter)
- Graph model: SymbolNode + typed edges (CALLS, IMPORTS, DEFINES, CONTAINS, INHERITS, IMPLEMENTS)
- Incremental indexing: mtime + sha256 per file, only re-parses changed files
- External symbol stub nodes — dependencies visible but not expanded
- Natural language queries via sentence-transformers + chromadb (local, no API key needed)
- Optional OpenAI integration for LLM-synthesized answers (`graphmy[openai]`)
- Self-contained HTML visualisation — cytoscape.js + dagre layout, full source inlined
- `--serve` mode: FastAPI server with live graph UI and NL query bar (`graphmy[serve]`)
- Detail panel: name, file:line, signature, docstring, callers, callees, source preview
- `--max-body-lines` flag for capping inlined source in large codebases
- CLI commands: `index`, `query`, `viz`, `info`, `config`, `--version`, `--help`
- Python API: `GraphmyIndex`, `GraphmyConfig`
- `.graphmy/config.toml` + environment variable configuration
- Auto-adds `.graphmy/` to project `.gitignore` on first index
- CI: lint, test (Python 3.10/3.11/3.12 matrix), publish (tag-triggered PyPI)
- Open-source: MIT license, CONTRIBUTING guide, issue templates, PR template
