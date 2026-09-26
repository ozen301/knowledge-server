# knowledge-server
MCP server for a personal knowledge base.

Tasks 1 and 2 provide the installable Python package, verify its MCP SDK
dependency, and define the shared configuration, typed models, limits, and
path policy. The server tools and knowledge-vault operations are not
implemented yet.

## Current core usage

The shared policy can be used by later operations after startup supplies an
explicit local checkout. This is a Python API example, not a server launch
command:

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
regular files. Future tasks add the read, list, info, and search operations.

## Development prerequisites

- Python 3.14 or later.
- uv for Python environment and dependency management.
- ripgrep (`rg`) when Task 4 adds literal text search.

## Development commands

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

The declared minimum is Python 3.14. Verify it with the uv-managed interpreter:

```sh
uv run --python 3.14 --locked --dev pytest
```

The resolved MCP SDK version and tested invocation are recorded in the
lockfile and below.

## SDK compatibility

The lockfile resolves `mcp` 2.2.0. The smoke test imports `MCPServer` from
`mcp.server.mcpserver` and `Client` plus `StdioServerParameters` from `mcp`.
It starts a temporary `MCPServer` with `server.run("stdio")`, connects a client
over stdio, confirms that no tools are registered, closes the client, and
verifies that the server returns cleanly.

## Project documents

- [Project glossary](CONTEXT.md): canonical terms for the vault and its local
  checkout.
- [Repository guide](AGENTS.md): development workflow and writing conventions.
- [Implementation plan](docs/implementation-plan.md): architecture, decisions, and progressive milestones.
- [Phase 1 contract](docs/phase-1-contract.md): agreed tool behavior and boundaries.
- [Implementation tasks](docs/implementation-tasks.md): bounded tasks and acceptance criteria for coding agents.
