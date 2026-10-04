# Usage

This guide explains how to connect knowledge-server to a local MCP host, how
to prepare the synthetic HTTP trial, and what the server can return. The
[README](../README.md#quick-start) has the short version. The [Phase 1
contract](phase-1-contract.md) defines the exact tool behavior.

For stdio, the MCP host starts the server as a subprocess and talks to it
over stdin and stdout; you do not start it yourself. The separate HTTP
launcher, which you start yourself, runs a protected loopback service for a
trial with invented notes. The Cloudflare route and the ChatGPT connection
still need provisioning and a live trial, as the [web access
plan](web-access.md) describes.

## Requirements

- A clone of this repository. The examples use `/path/to/knowledge-server`;
  replace it with the absolute path of your clone.
- uv. It installs Python 3.14 and the locked dependencies when the command
  first runs.
- ripgrep (`rg`) on the `PATH` that the host gives the server.
- A local vault checkout. The examples use `/path/to/knowledge-vault`;
  replace it with its absolute path.

## Check the launch command

Run the command from any directory, with stdin closed:

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
line 7.

If the host cannot find `uv`, use its absolute path as the command. If the
server reports that `rg` is missing, add a `PATH` value that contains `rg` to
the server's environment.

## Prepare the synthetic HTTP trial

`knowledge-server-http` serves the same four tools at `/mcp` on IPv4
loopback, for a trial with the invented sample notes only. Every request
needs a valid signed Cloudflare assertion for the pinned owner subject; the
opaque OAuth access token alone is not enough. The public route and ChatGPT
compatibility are not yet verified. The [HTTP
contract](web-access.md#local-http-implementation-contract) defines the exact
settings and checks.

1. Copy the sample notes to a new, dedicated directory:

   ```sh
   cp -R /path/to/knowledge-server/tests/fixtures/vault \
     /absolute/path/to/invented-notes
   ```

   Keep only these invented notes in the directory for as long as you use
   it. At startup, the launcher compares the directory with a packaged
   SHA-256 manifest and rejects changed, missing, or extra files, symlinks,
   and special files. It does not detect later changes, and the tools read
   the directory as it is at each request.
2. During authorized provisioning, obtain the Access application's AUD tag
   and privately establish the owner subject as the [owner identity
   procedure](web-access.md#owner-identity) requires. That
   procedure is not yet written. Do not paste assertions, claims, or
   credentials into chat. The launcher does not start without an owner
   subject, so no tool can be used before it is pinned.
3. Save this TOML in a private file outside the repository. Set `root` to
   the directory from step 1. Replace `team_domain` and `public_host` with
   your Cloudflare team domain and MCP hostname, and replace the audience
   and owner placeholders with the values from step 2. Restrict the file to
   your account, for example with `chmod 600 /absolute/private/trial.toml`.

   ```toml
   mode = "synthetic"
   root = "/absolute/path/to/invented-notes"
   team_domain = "example.cloudflareaccess.com"
   audience = "<application-aud>"
   owner_subject = "<verified-owner-sub>"
   public_host = "mcp.example.com"
   port = 8000
   allowed_origins = []
   ```

   `allowed_origins` accepts exact values only. A request without `Origin`
   is accepted; with this empty list, any request that sends `Origin` is
   rejected. Add an origin only after verifying that the chosen client needs
   it.
4. From a revision whose local validation and diff review passed, start the
   launcher:

   ```sh
   uv run --project /path/to/knowledge-server --locked \
     knowledge-server-http --config /absolute/private/trial.toml
   ```

   Expected result: the process keeps running and listens only on
   `127.0.0.1:8000`. It does not interpret proxy headers, never treats
   unsigned identity headers as authentication, and ignores `KNOWLEDGE_ROOT`
   and `.env`. Stderr receives one line per request with only the method,
   status, and latency.

   If startup fails, the process exits with status `1` and writes one line,
   `knowledge-server-http startup category=<category>`, to stderr:

   | Category | Cause |
   |---|---|
   | `configuration` | `--config` is missing; the file is unreadable, larger than 16 KiB, or invalid; or the directory fails the manifest check. |
   | `missing-ripgrep` | `rg` is not on `PATH`. |
   | `runtime` | The server could not start, for example because the port is in use, or it failed unexpectedly. |

5. From another terminal, check that the gate rejects a request without an
   assertion:

   ```sh
   curl -si http://127.0.0.1:8000/mcp
   ```

   If you set another `port`, use it instead of `8000`. Expected result:
   status 401 with the header `cache-control: no-store`. A request with an
   unlisted Host receives 421, and one with an unapproved Origin receives 403.
6. To stop the process, press Ctrl-C. It shuts down and exits with status
   `0`. After SIGTERM, it also shuts down gracefully, and then ends by that
   signal, which a shell reports as status `143`.

Configure Access, Managed OAuth, Tunnel, and cloudflared only in the
authorized provisioning step, and keep the public route disabled until the
owner subject is established. The Cloudflare edge serves OAuth discovery; the
origin implements no OAuth discovery or registration. The launcher has no
real-vault mode; Tasks 8–9 prepare and authorize that deployment.

## What the server reads

- **The live working tree.** The server reads the files in the local vault
  checkout as they are now, including uncommitted and untracked notes. An
  edit is visible in the next tool call without a restart. The server does
  not search Git history or cache content. `KNOWLEDGE_ROOT` is resolved once
  at startup, so restart the host after moving the vault. The HTTP launcher
  instead reads its root from its private configuration file at startup;
  keep that directory dedicated to the invented notes.
- **No Git operations.** The server never writes files and never runs Git.
  You synchronize the local vault checkout with Git yourself. Results during a
  pull or checkout can mix old and new files; repeat the question afterwards.
- **Visible notes only.** All tools see regular files with a `.md` suffix
  (any letter case) in non-hidden directories. They exclude hidden names that
  start with `.`, such as `.git` and `.obsidian`, symlinks, special files, and
  other file types. Git ignore rules do not hide a note.
- **Literal search.** `knowledge_search` finds lines that contain the query
  text exactly, ignoring letter case unless the caller asks otherwise. It does
  not split words, rank results, or find synonyms, so an agent may need
  several queries with different wording. Visually identical Unicode text in
  different forms, such as a precomposed and a combining accent, does not
  match. Full-width and half-width forms, such as `＋` and `+`, do not match
  each other either.
- **Numbered lines.** `knowledge_read` returns each line as its line number,
  a tab, and the text, such as `7\t- **CPU:** AMD Ryzen 5 2600X`. Agents cite
  these numbers. Search matches carry their line number in a separate field.
- **Limits.** Notes larger than 1 MiB are listed but not read or searched. A
  search returns at most 50 matches (20 by default) and stops after 10
  seconds. A read returns at most 200 lines and 32 KiB of line text; the
  line-number prefixes come on top of that. The
  [contract](phase-1-contract.md#initial-limits) lists every limit.

## Data disclosure

The host sends tool results, including note excerpts and paths, to its model
provider as part of the conversation. Any visible note under `KNOWLEDGE_ROOT`
can be returned, so set the root to a directory whose notes you are willing to
send to that provider. The provider's terms and your account settings decide
how long it keeps them. The hosts also save session transcripts locally:
Claude Code under `~/.claude/projects/` and Codex under `~/.codex/sessions/`.
The server itself writes only short diagnostic messages to stderr, never note
contents or queries. In the planned web route, Cloudflare also handles
decrypted requests and responses, and ChatGPT sends tool results to OpenAI.
See the [web trust boundary](web-access.md#trust-boundary-and-data-handling)
before provisioning or considering real-vault use.

## Use the core from Python

The MCP tools call core operations that are also available as a Python API.
This example is not a server launch command:

```python
from knowledge_server.config import load_config
from knowledge_server.core.models import ReadRequest
from knowledge_server.core.paths import PathPolicy
from knowledge_server.core.reader import read_note

config = load_config({"KNOWLEDGE_ROOT": "/path/to/knowledge-vault"})
policy = PathPolicy(config.root)
request = ReadRequest(path="infrastructure/nas-configuration.md", end_line=20)
result = read_note(policy, request)
print(result.numbered_content, result.next_line)
```

`read_note`, `list_directory`, and `note_info` in
`knowledge_server.core.reader` implement the read, list, and info tools. They
raise `KnowledgeError` with a contract error code when a request fails.

`search_notes` in `knowledge_server.core.search` implements search. It is an
`async` function and needs the path of the ripgrep executable:

```python
import asyncio
import shutil

from knowledge_server.core.models import SearchRequest
from knowledge_server.core.search import search_notes

request = SearchRequest(query="ECC")
result = asyncio.run(search_notes(policy, request, ripgrep=shutil.which("rg")))
for match in result.matches:
    print(match.path, match.line, match.snippet)
```
