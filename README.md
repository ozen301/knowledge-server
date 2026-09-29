# knowledge-server

A read-only MCP server that will let an agent search a personal knowledge base
of Markdown notes, read the relevant lines, and cite the source note. It will
read a local Git checkout of the notes without modifying them.

## Status

The project is under development. The installable Python package, shared
configuration, typed models, limits, path policy, and the core search, read,
list, and info operations exist. The `knowledge-server` command serves the
four MCP tools over stdio; instructions for registering it with an MCP host
are not written yet. The [implementation tasks](docs/implementation-tasks.md)
track progress.

## Current core API

The configuration, path policy, and core operations are available as a
Python API, and the MCP tools call these operations. This example is not a
server launch command:

```python
from knowledge_server.config import load_config
from knowledge_server.core.models import ReadRequest
from knowledge_server.core.paths import PathPolicy
from knowledge_server.core.reader import read_note

config = load_config({"KNOWLEDGE_ROOT": "/path/to/knowledge-vault"})
policy = PathPolicy(config.root)
result = read_note(policy, ReadRequest(path="Projects/roadmap.md", end_line=20))
print(result.content, result.next_line)
```

`read_note`, `list_directory`, and `note_info` in
`knowledge_server.core.reader` implement the read, list, and info behavior in
the [specification](docs/phase-1-contract.md). They raise `KnowledgeError`
with a contract error code when a request fails.

`search_notes` in `knowledge_server.core.search` implements search. It is an
`async` function and needs the path of the ripgrep executable:

```python
import asyncio
import shutil

from knowledge_server.core.models import SearchRequest
from knowledge_server.core.search import search_notes

request = SearchRequest(query="ECC memory")
result = asyncio.run(search_notes(policy, request, ripgrep=shutil.which("rg")))
for match in result.matches:
    print(match.path, match.line, match.snippet)
```

Paths are root-relative and use `/`. The policy permits non-hidden Markdown
files regardless of Git ignore rules, so an ignored Markdown note remains
eligible. It excludes hidden names, symlinks, special files, and non-Markdown
regular files.

## Development prerequisites

- Python 3.14 or later.
- uv for Python environment and dependency management.
- ripgrep (`rg`) for literal text search. The search tests run it and fail
  if it is not installed.

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
- [Architecture overview](docs/architecture.md): what the components do and
  how a request moves through them.
- [Repository guide](AGENTS.md): development workflow and writing conventions.
- [Implementation plan](docs/implementation-plan.md): architecture, decisions,
  and milestones.
- [Phase 1 contract](docs/phase-1-contract.md): agreed tool behavior and
  boundaries.
- [Implementation tasks](docs/implementation-tasks.md): ordered tasks,
  acceptance criteria, and progress.
