# knowledge-server
MCP server for a personal knowledge base.

Task 1 provides the installable Python package and verifies its MCP SDK
dependency. The server tools and knowledge-vault access are not implemented
yet.

## Development prerequisites

- Python 3.13 or later.
- uv for Python environment and dependency management.
- ripgrep (`rg`) when Task 4 adds literal text search.

## Development commands

Run these commands from the repository root. They install the pinned dependency
set and verify the package, SDK smoke check, formatting, linting, and types:

```sh
uv sync --locked --dev
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

The declared minimum is Python 3.13. Verify it with the uv-managed interpreter:

```sh
uv run --python 3.13 --locked --dev pytest
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
