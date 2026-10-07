# Knowledge Server

An [MCP](https://modelcontextprotocol.io/) server that lets AI agents search
a personal collection of Markdown notes, read the lines that matter, and cite
where they found them.

The notes are a folder of Markdown files kept in Git, called the "vault" as in
[Obsidian](https://obsidian.md/). Knowledge Server works with local agents such
as Claude Code and Codex CLI, and with web clients such as ChatGPT and
Claude.ai. Agents can read the notes, and web clients can also propose new
notes and edits (local agents can already edit files with their own tools). A
proposal takes effect only after the vault owner reviews and merges it.

## Quick start

### Local agents

The server needs [uv](https://docs.astral.sh/uv/), ripgrep (`rg`) on `PATH`,
a clone of this repository, and a local checkout of the notes. Replace the
example paths with absolute paths.

1. Register the server with the MCP host. The host starts it when needed.

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

2. Ask the agent a question about the notes, and ask it to cite its source.
   It searches, reads the relevant lines, and cites the note path and line.

The host sends the note excerpts it receives to its model provider. The
[guide for local MCP hosts](docs/use-with-local-hosts.md) covers registration
details, troubleshooting, and how the tools behave.

### Web clients

ChatGPT and Claude.ai reach the server over HTTPS, so it runs as a service.
The [guide for web clients](docs/use-with-web-clients.md) sets it up on a
small Linux VM behind Cloudflare Tunnel and Cloudflare Access, connects both
clients, and enables write proposals and their review. Cloudflare and the
client's provider, OpenAI or Anthropic, handle the note excerpts that the
client receives.

## Documentation

### Guides

- [Use with local MCP hosts](docs/use-with-local-hosts.md): connecting Claude
  Code or Codex CLI so that they can search and read the notes. It also
  describes how the tools behave for every client.
- [Use with web clients](docs/use-with-web-clients.md): connecting ChatGPT and
  Claude.ai through a server running in a VM, so that they can search, read,
  and propose changes to the notes.

### Design

- [Architecture overview](docs/architecture.md): the components, what each
  one does, and how they work together.
- [Design decisions](docs/design-decisions.md): why the server works as it
  does, and the risks it accepts.

### Specifications

- [Tool contract](docs/tool-contract.md): the exact behavior, limits, and
  errors of each tool.
- [HTTP contract](docs/http-contract.md): the HTTP entry point's
  configuration, the checks it applies to each request, and its logging.

### Project maintenance

- [Repository guide](AGENTS.md): the development workflow and writing
  conventions that coding agents follow.
- [Glossary](GLOSSARY.md): the project's terms, such as "knowledge vault",
  "local vault checkout", and "vault owner" .
- [Roadmap](docs/roadmap.md): possible next steps, and how to plan and finish
  a task.
- [Sample notes and retrieval questions](docs/sample-notes.md): the invented
  notes that the tests use, fixed questions that check retrieval quality, and
  the known weaknesses of search.

## Development

Development needs Python 3.14 or later, uv, and ripgrep; the search tests
run `rg` and fail without it. The tests use the invented notes in
`tests/fixtures/vault/` and need no real vault, accounts, or network access.

Run all checks from the repository root:

```sh
scripts/check
```

The script installs the locked dependencies, runs the formatting, lint, type,
and test checks, checks for whitespace errors, and prints a summary. To run a
single check, use its command:

```sh
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

GitHub Actions runs `scripts/check --ci` on every push to `main`, and on
other branches only on request, so run the script locally before merging into
`main`.

## Status

Knowledge Server is a personal project in regular use. The
[roadmap](docs/roadmap.md) lists possible next steps.
