# Knowledge Server

Let AI agents search your Markdown notes, read the lines that matter, and
cite where they found them.

Knowledge Server is an [MCP](https://modelcontextprotocol.io/) server for a
personal knowledge base: a folder of Markdown notes that you keep in Git. It
works with local agents such as Claude Code and Codex CLI, and with web
clients such as ChatGPT and Claude.ai. Agents read the notes but cannot edit
them. Web clients can also propose new notes and edits, which take effect
only when you review and merge them.

## Quick start

You need [uv](https://docs.astral.sh/uv/), ripgrep (`rg`) on `PATH`, a clone
of this repository, and a local checkout of your notes. Replace the example
paths with absolute paths.

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

2. Ask the agent a question about your notes, and ask it to cite its source.
   It searches, reads the relevant lines, and cites the note path and line.

The host sends the note excerpts it receives to its model provider. The
[guide for local MCP hosts](docs/use-with-local-hosts.md) covers registration
details, troubleshooting, and how the tools behave. To use the notes from
ChatGPT or Claude.ai, follow the [guide for web
clients](docs/use-with-web-clients.md); it sets up the HTTP service on a
small Linux VM behind Cloudflare.

## Documentation

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
- [Roadmap](docs/roadmap.md): possible next steps and how to start a task.
- [Sample notes and retrieval questions](docs/sample-notes.md): the invented
  test notes, the fixed questions about them, and the known retrieval
  weaknesses.

Project conventions:

- [Glossary](GLOSSARY.md): the terms for the vault, its local checkout, and
  project roles.
- [Repository guide](AGENTS.md): development workflow and writing
  conventions.

## Development

You need Python 3.14 or later, uv, and ripgrep; the search tests run `rg` and
fail without it. From the repository root, run:

```sh
scripts/check
```

It installs the locked dependencies, runs the formatting, lint, type, and
test checks, checks for whitespace errors, and summarizes the results. You
can also run its main commands one at a time:

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

## Status

Knowledge Server is a personal project, built for one vault owner and in
regular use. The [roadmap](docs/roadmap.md) lists possible next steps.
