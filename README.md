# knowledge-server

An MCP server that lets an agent search a personal knowledge base of Markdown
notes, read the relevant lines, and cite the source note. It reads a local Git
checkout of the notes without modifying them. Over HTTP, it can also save new
notes and edits as proposals that the vault owner reviews and merges.

## Features

- Four read-only MCP tools: `knowledge_search` (literal phrase search),
  `knowledge_read` (line ranges with line numbers for citation),
  `knowledge_list`, and `knowledge_info`.
- One visibility policy for every tool: only regular `.md` notes, with no
  hidden paths or symlinks, and fixed limits on the size of results.
- Reads the local vault checkout as it is now and never runs Git.
- Optional write proposals over HTTP: `knowledge_propose_note` and
  `knowledge_propose_edit` save new notes and exact text replacements in an
  `inbox/` directory. They never change a note; the vault owner merges
  proposals by hand.
- Two ways to connect:
  - `knowledge-server` serves local MCP hosts over stdio. It has been checked
    with Claude Code and Codex CLI.
  - `knowledge-server-http` is a protected HTTP service for remote clients
    such as ChatGPT and Claude.ai. It accepts only requests that Cloudflare
    Access signed for the vault owner, and includes a systemd unit.

The first version is complete. The [roadmap](docs/roadmap.md) lists optional
next steps.

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
[guide for local MCP hosts](docs/use-with-local-hosts.md) covers registration
details, troubleshooting, and how the tools behave.

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
`main`, so run the script locally before merging into `main`. CI installs
ripgrep from the Ubuntu packages, so it can test a different ripgrep version
than your machine.

## Project documents

Guides:

- [Use with local MCP hosts](docs/use-with-local-hosts.md): registration
  over stdio, troubleshooting, and how the tools behave and their limits.
- [Use with web clients](docs/use-with-web-clients.md): setting up the remote
  route for ChatGPT and Claude.ai in the VM, write proposals and their
  review, daily operation, updates, the emergency stop, and troubleshooting.

Specifications:

- [Tool contract](docs/tool-contract.md): exact tool behavior, limits, and
  errors.
- [HTTP contract](docs/http-contract.md): the HTTP entry point's
  configuration, request checks, assertion rules, and logging.

Design and planning:

- [Architecture overview](docs/architecture.md): what the components do and
  how they work together.
- [Design decisions](docs/design-decisions.md): why the server works as it
  does, the remote route, and its trust boundary.
- [Roadmap](docs/roadmap.md): optional later work and how to start a task.
- [Sample notes and retrieval questions](docs/sample-notes.md): the invented
  test notes, the fixed questions about them, and the known retrieval
  weaknesses.

Project conventions:

- [Project glossary](GLOSSARY.md): canonical terms for the vault, its local
  checkout, and project roles.
- [Repository guide](AGENTS.md): development workflow and writing conventions.
