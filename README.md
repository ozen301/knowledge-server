# knowledge-server

A read-only MCP server that will let an agent search a personal knowledge base
of Markdown notes, read the relevant lines, and cite the source note. It will
read a local Git checkout of the notes without modifying them.

## Status

The project is under development. The installable Python package, shared
configuration, typed models, limits, and path policy exist; the MCP server
tools are not implemented yet. The
[implementation tasks](docs/implementation-tasks.md) track progress.

## Current core API

The configuration and path policy are available as a Python API for the read,
list, info, and search operations that later tasks add. This example is not a
server launch command:

```python
from knowledge_server.config import load_config
from knowledge_server.core.paths import PathPolicy, TargetKind

config = load_config({"KNOWLEDGE_ROOT": "/path/to/knowledge-vault"})
policy = PathPolicy(config.root)
note = policy.resolve("Projects/roadmap.md", TargetKind.FILE)
```

Paths are root-relative and use `/`. The policy permits non-hidden Markdown
files regardless of Git ignore rules, so an ignored Markdown note remains
eligible. It excludes hidden names, symlinks, special files, and non-Markdown
regular files.

## Development prerequisites

- Python 3.14 or later.
- uv for Python environment and dependency management.
- ripgrep (`rg`) when Task 4 adds literal text search.

## Validation

Run the validation script from the repository root. It installs the pinned
dependency set, runs formatting, lint, type, and test checks, checks for
whitespace errors, and summarizes the results:

```sh
scripts/check
```

You can also run its main commands individually:

```sh
uv sync --locked --dev
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

GitHub Actions runs `scripts/check --ci` on pushes to `main` only, so run the
script locally before merging `dev`.

## Project documents

- [Project glossary](CONTEXT.md): canonical terms for the vault, its local
  checkout, and project roles.
- [Repository guide](AGENTS.md): development workflow and writing conventions.
- [Implementation plan](docs/implementation-plan.md): architecture, decisions,
  and milestones.
- [Phase 1 contract](docs/phase-1-contract.md): agreed tool behavior and
  boundaries.
- [Implementation tasks](docs/implementation-tasks.md): ordered tasks,
  acceptance criteria, and progress.
