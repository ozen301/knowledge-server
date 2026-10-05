# knowledge-server

A read-only MCP server that lets an agent search a personal knowledge base of
Markdown notes, read the relevant lines, and cite the source note. It reads a
local Git checkout of the notes without modifying them.

## Status

The project is under development. The `knowledge-server` command serves four
read-only MCP tools over stdio to a local MCP host: `knowledge_search`,
`knowledge_read`, `knowledge_list`, and `knowledge_info`. It has been checked
with Claude Code and Codex CLI.

`knowledge-server-http` serves the same tools over HTTP on loopback. It
accepts only requests that carry a valid Cloudflare Access assertion for the
pinned vault owner. Through Cloudflare Access Managed OAuth and Tunnel,
ChatGPT has used it to answer questions about the sample notes and cite the
source note and line. It has a synthetic mode and an explicit real-vault
mode, and `deploy/` holds a systemd unit for the VM; real notes are not
served until the vault owner authorizes a scope. The [usage
guide](docs/usage.md) describes the launcher and the service runbook, and
the [implementation tasks](docs/implementation-tasks.md) track progress and
record the trial evidence.

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

- [Project glossary](GLOSSARY.md): canonical terms for the vault, its local
  checkout, and project roles.
- [Architecture overview](docs/architecture.md): what the components do and
  how they work together.
- [Usage guide](docs/usage.md): host registration, the synthetic HTTP trial,
  the VM service runbook, behavior, limits, and data disclosure.
- [Repository guide](AGENTS.md): development workflow and writing conventions.
- [Implementation plan](docs/implementation-plan.md): architecture, decisions,
  and milestones.
- [Phase 1 contract](docs/phase-1-contract.md): agreed tool behavior and
  boundaries.
- [Web access plan](docs/web-access.md): the HTTP security contract and remote
  route for ChatGPT, including the OAuth settings validated in the trial.
- [Implementation tasks](docs/implementation-tasks.md): ordered tasks,
  acceptance criteria, and progress.
- [Retrieval evaluation](docs/retrieval-evaluation.md): fixed questions about
  the sample notes and the results from local hosts.
