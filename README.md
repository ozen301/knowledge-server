# knowledge-server

A read-only MCP server that lets an agent search a personal knowledge base of
Markdown notes, read the relevant lines, and cite the source note. It reads a
local Git checkout of the notes without modifying them.

## Status

The project is under development. The `knowledge-server` command serves four
read-only MCP tools over stdio to a local MCP host: `knowledge_search`,
`knowledge_read`, `knowledge_list`, and `knowledge_info`. It has been checked
with Claude Code and Codex CLI. Web access for ChatGPT, through Cloudflare
Access and Cloudflare Tunnel, is planned in the [web access
plan](docs/web-access.md) but not implemented. The [implementation
tasks](docs/implementation-tasks.md) track progress.

## Quick start

You need uv, ripgrep (`rg`) on `PATH`, a clone of this repository, and a
local vault checkout. Replace the example paths with absolute paths.

1. Register the server with your MCP host. The host starts it when needed.

   Claude Code:

   ```sh
   claude mcp add --scope user knowledge \
     -e KNOWLEDGE_ROOT=/path/to/knowledge-vault \
     -- uv run --project /path/to/knowledge-server --locked knowledge-server
   ```

   Codex CLI:

   ```sh
   codex mcp add knowledge \
     --env KNOWLEDGE_ROOT=/path/to/knowledge-vault \
     -- uv run --project /path/to/knowledge-server --locked knowledge-server
   ```

2. Ask the agent a question about your notes and to cite its source. It
   searches, reads the relevant lines, and cites the note path and line.

The host sends the note excerpts it receives to its model provider. The
[usage guide](docs/usage.md) explains what the server reads, its limits,
data disclosure, troubleshooting, and the Python API.

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

GitHub Actions runs `scripts/check --ci` automatically only on pushes to
`main`, so run the script locally before merging `dev`.

## Project documents

- [Project glossary](CONTEXT.md): canonical terms for the vault, its local
  checkout, and project roles.
- [Architecture overview](docs/architecture.md): what the components do and
  how they work together.
- [Usage guide](docs/usage.md): host registration, behavior, limits, and
  data disclosure.
- [Repository guide](AGENTS.md): development workflow and writing conventions.
- [Implementation plan](docs/implementation-plan.md): architecture, decisions,
  and milestones.
- [Phase 1 contract](docs/phase-1-contract.md): agreed tool behavior and
  boundaries.
- [Web access plan](docs/web-access.md): the planned remote route for
  ChatGPT, not yet implemented.
- [Implementation tasks](docs/implementation-tasks.md): ordered tasks,
  acceptance criteria, and progress.
- [Retrieval evaluation](docs/retrieval-evaluation.md): fixed questions about
  the sample notes and the results from local hosts.
