# graphmy

**Parse any codebase, build a knowledge graph, visualise it, and query it in natural language.**

[![PyPI version](https://badge.fury.io/py/graphmy.svg)](https://badge.fury.io/py/graphmy)
[![CI](https://github.com/Kamalesh-Kavin/graphmy/actions/workflows/test.yml/badge.svg)](https://github.com/Kamalesh-Kavin/graphmy/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

`graphmy` turns any source code directory into an interactive, queryable knowledge graph. Point it at a codebase and get:

- **A navigable graph** — every function, class, method, and file as a node; every call, import, and inheritance as a typed edge
- **A self-contained HTML visualisation** — open in any browser, share as a single file, no server required
- **Natural language queries** — "what calls authenticate?", "find functions related to payment processing"
- **Multi-language** — Python, JavaScript, TypeScript, Go, Rust, and Java in one tool

---

## Install

```bash
pip install graphmy
```

For the `--serve` live UI:
```bash
pip install graphmy[serve]
```

For LLM-synthesized query answers (requires OpenAI key):
```bash
pip install graphmy[openai]
```

---

## Quick start

```bash
# 1. Index a codebase (builds graph + vector index in .graphmy/)
graphmy index ./my-project

# 2. Query in natural language
graphmy query ./my-project "what calls authenticate?"
graphmy query ./my-project "find functions related to payments"

# 3. Generate interactive HTML visualisation (self-contained, ~2MB+)
graphmy viz ./my-project
open output.html

# 4. Or boot a live server with NL query bar
graphmy viz ./my-project --serve

# 5. Inspect codebase stats
graphmy info ./my-project
```

---

## How it works

```
┌─────────────────────────────────────────────────────┐
│  PARSER  (tree-sitter, per-language grammars)        │
│  • functions, classes, methods, imports, inheritance │
│  • works on Python, JS/TS, Go, Rust, Java           │
├─────────────────────────────────────────────────────┤
│  GRAPH   (networkx DiGraph)                          │
│  • nodes: File, Class, Function, Method, …           │
│  • edges: CALLS, IMPORTS, DEFINES, CONTAINS,         │
│           INHERITS, IMPLEMENTS                       │
│  • persisted as JSON in .graphmy/                    │
├─────────────────────────────────────────────────────┤
│  SEARCH  (sentence-transformers + chromadb)          │
│  • embeds every symbol (name + signature + docstring)│
│  • incremental upsert — only re-embeds changed files │
│  • structural queries bypass embeddings entirely     │
├─────────────────────────────────────────────────────┤
│  VISUALISER  (cytoscape.js + dagre layout)           │
│  • self-contained HTML (no server needed)            │
│  • click any node → name, file:line, source preview, │
│    callers, callees                                  │
│  • --serve boots FastAPI + NL query bar              │
└─────────────────────────────────────────────────────┘
```

---

## CLI reference

```
graphmy index   <path> [--exclude GLOB] [--fresh]
graphmy query   <path> <query> [--limit N] [--explain]
graphmy viz     <path> [--out FILE] [--serve] [--host H] [--port P]
                       [--max-body-lines N]
graphmy info    <path>
graphmy config  <path>
graphmy --version
graphmy --help
```

### `graphmy index`

Parse the codebase and build the graph + vector index. Stores everything in `.graphmy/` at the project root. Subsequent runs are incremental — only changed files are re-parsed.

```bash
graphmy index ./my-project
graphmy index ./my-project --exclude "tests/**" --exclude "**/*.min.js"
graphmy index ./my-project --fresh   # ignore cache, full re-index
```

### `graphmy query`

Search the codebase in natural language.

```bash
graphmy query ./my-project "what calls validate_user?"
graphmy query ./my-project "find authentication functions" --limit 10
graphmy query ./my-project "explain the payment flow" --explain  # requires openai extra
```

### `graphmy viz`

Generate an interactive visualisation.

```bash
# Self-contained HTML file (default)
graphmy viz ./my-project
graphmy viz ./my-project --out my-graph.html
graphmy viz ./my-project --max-body-lines 50   # cap inlined source for large repos

# Live server with NL query bar
graphmy viz ./my-project --serve
graphmy viz ./my-project --serve --host 0.0.0.0 --port 8080
```

---

## Configuration

Create `.graphmy/config.toml` in your project root, or use environment variables:

```toml
# .graphmy/config.toml

# OpenAI integration (enables --explain in queries and the Explain button in --serve UI)
openai_api_key = "sk-..."
openai_model   = "gpt-4o-mini"   # default

# Paths to exclude from indexing (glob patterns, relative to project root)
exclude = ["tests/**", "docs/**", "**/*.min.js", "**/node_modules/**"]

# Maximum source lines inlined per symbol in static HTML (0 = unlimited)
max_body_lines = 0
```

Environment variables take precedence over config file:
```bash
export GRAPHMY_OPENAI_API_KEY="sk-..."
export GRAPHMY_OPENAI_MODEL="gpt-4o"
```

---

## Python API

`graphmy` can also be used programmatically:

```python
from graphmy import GraphmyIndex, GraphmyConfig

# Index a codebase
config = GraphmyConfig(exclude=["tests/**"])
index = GraphmyIndex("./my-project", config=config)
index.build()   # incremental by default

# Structural query — exact graph traversal
results = index.query_structural("authenticate")
for r in results:
    print(r.name, r.file, r.line)

# Natural language query
results = index.query_nl("what handles user authentication?")
for r in results:
    print(r.symbol.name, r.score, r.relationships)

# Export visualisation
index.export_html("graph.html")
```

---

## Supported languages

| Language | Extensions | Extracts |
|---|---|---|
| Python | `.py` | functions, classes, methods, imports, inheritance, decorators, async |
| JavaScript | `.js` `.mjs` `.cjs` | functions, classes, methods, ESM imports, `require()` |
| TypeScript | `.ts` `.tsx` | all JS + interfaces, type aliases, enums, `implements` |
| Go | `.go` | functions, methods on types, structs, interfaces, imports |
| Rust | `.rs` | functions, structs, enums, traits, impl blocks, `use` statements |
| Java | `.java` | classes, interfaces, methods, constructors, `extends`, `implements` |

External symbols (calls/imports to dependencies outside the project root) appear as **stub nodes** — visible in the graph but not expanded. This keeps your graph focused on your code.

---

## Index cache (`.graphmy/`)

```
<project-root>/
└── .graphmy/
    ├── config.toml          # optional user config
    ├── graph.json           # full graph (networkx node-link JSON)
    ├── file_hashes.json     # {filepath: [mtime, sha256]} for incremental re-index
    └── vectors/             # chromadb embedded vector store (SQLite + HNSW)
```

Add `.graphmy/` to your `.gitignore` (graphmy does this automatically on first index).

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and PRs are welcome.

---

## License

MIT — see [LICENSE](LICENSE).
