# Use with local MCP hosts

This guide explains how to connect knowledge-server to a local MCP host, such
as Claude Code or Codex CLI, over stdio. Its last section, [how the tools
behave](#how-the-tools-behave), applies to every client. The
[README](../README.md#quick-start) has the short version, and the [tool
contract](tool-contract.md) defines the exact tool behavior.

The MCP host starts the server as a subprocess and talks to it over stdin and
stdout; you do not start it yourself. Web clients such as ChatGPT use the same
tools through a separate, protected HTTP service; the [guide for web
clients](use-with-web-clients.md) sets it up and operates it.

## Requirements

- A clone of this repository. The examples use `/path/to/knowledge-server`;
  replace it with the absolute path of your clone.
- uv. It installs Python 3.14 and the locked dependencies when the command
  first runs.
- ripgrep (`rg`) on the `PATH` that the host gives the server.
- A local vault checkout. The examples use `/path/to/knowledge-vault`;
  replace it with its absolute path.

## Register with Claude Code

```sh
claude mcp add --scope user knowledge \
  -e KNOWLEDGE_ROOT=/path/to/knowledge-vault \
  -- uv run --project /path/to/knowledge-server --locked knowledge-server
```

`--scope user` makes the server available in every directory and stores it in
`~/.claude.json`. Without it, the server is available only in the directory
where you ran the command. Do not use `--scope project` in this repository,
because it writes an `.mcp.json` file into the working directory. `claude mcp
list` starts the server and shows `✔ Connected` when it works; in a session,
`/mcp` shows the same status.

Claude Code asks for permission before it calls a tool. The tool names have
the prefix `mcp__knowledge__`, for example `mcp__knowledge__knowledge_search`.

To remove the server: `claude mcp remove --scope user knowledge`.

## Register with Codex CLI

```sh
codex mcp add knowledge \
  --env KNOWLEDGE_ROOT=/path/to/knowledge-vault \
  -- uv run --project /path/to/knowledge-server --locked knowledge-server
```

This adds the following entry to `~/.codex/config.toml`, which you can also
write by hand:

```toml
[mcp_servers.knowledge]
command = "uv"
args = ["run", "--project", "/path/to/knowledge-server", "--locked", "knowledge-server"]

[mcp_servers.knowledge.env]
KNOWLEDGE_ROOT = "/path/to/knowledge-vault"
```

`codex mcp list` shows the configured servers, but not whether the server
starts, so check the connection with a question as described below. To remove
the server: `codex mcp remove knowledge`.

## Check the connection

Ask the agent a question whose answer is in one of your notes. With the
sample notes in `tests/fixtures/vault/` as the root, ask: "Using my knowledge
vault, which CPU does my NAS use? Cite the note and line." A working setup
searches with `knowledge_search`, reads the note with `knowledge_read`, and
answers "AMD Ryzen 5 2600X", citing `infrastructure/nas-configuration.md`,
line 7. If it does not, see [Troubleshooting](#troubleshooting).

## Troubleshooting

Check the launch command by running it from any directory, with stdin closed:

```sh
KNOWLEDGE_ROOT=/path/to/knowledge-vault \
  uv run --project /path/to/knowledge-server --locked knowledge-server \
  < /dev/null
echo $?
```

Expected result: no output, and exit status `0`. The server stops when stdin
closes. If the configuration is wrong, the server writes one line to stderr,
in the form `knowledge-server ERROR knowledge_server: <message>`, and exits
with status `1`:

| Message | Cause |
|---|---|
| `KNOWLEDGE_ROOT must name an absolute readable directory.` | The variable is not set, is empty, or is a relative path. |
| `KNOWLEDGE_ROOT must name an existing readable directory.` | The path does not exist, is not a directory, or cannot be read and entered. |
| `KNOWLEDGE_ROOT could not be opened.` | The directory passed the checks above but could not be opened. |
| `ripgrep (rg) was not found on PATH.` | `rg` is not installed or not on `PATH`. |

If the command works by hand but not in the host:

- If the host cannot find `uv`, use its absolute path as the command.
- If the server reports that `rg` is missing, add a `PATH` value that
  contains `rg` to the server's environment.

## How the tools behave

- **The live working tree.** The server reads the files in the local vault
  checkout as they are now, including uncommitted and untracked notes. An
  edit is visible in the next tool call without a restart. The server does
  not search Git history or cache content. The root is resolved once at
  startup, so restart the host, or the HTTP service, after moving the vault.
- **No note changes or Git operations.** The server never changes a note and
  never runs Git. Over stdio it writes no files at all; over HTTP, web clients
  can save [write proposals](use-with-web-clients.md#enable-write-proposals) in
  an inbox for you to review. You synchronize the local vault checkout with Git
  yourself. Results during a pull or checkout can mix old and new files; repeat
  the question afterwards.
- **Visible notes only.** All tools see regular files with a `.md` suffix
  (any letter case) in non-hidden directories. They exclude hidden names that
  start with `.`, such as `.git` and `.obsidian`, symlinks, special files, and
  other file types. Git ignore rules do not hide a note.
- **Literal search.** `knowledge_search` finds lines that contain a query text
  exactly, ignoring letter case unless the caller asks otherwise. It does not
  split words, rank results, or find synonyms, so an agent tries several
  wordings. One call accepts up to five queries and returns the lines that
  contain any of them, with one result limit for all. Visually identical Unicode
  text in different forms, such as a precomposed and a combining accent, does
  not match. Full-width and half-width forms, such as `＋` and `+`, do not match
  each other either.
- **Numbered lines.** `knowledge_read` returns each line as its line number,
  a tab, and the text, such as `7\t- **CPU:** AMD Ryzen 5 2600X`. Agents cite
  these numbers. Search matches carry their line number in a separate field.
- **Limits.** Notes larger than 1 MiB are listed but not read or searched. A
  search returns at most 50 matches (20 by default) and stops after 10
  seconds. A read returns at most 200 lines and 32 KiB of line text; the
  line-number prefixes come on top of that. The
  [contract](tool-contract.md#initial-limits) lists every limit.
