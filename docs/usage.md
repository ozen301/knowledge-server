# Usage

This guide explains how to connect knowledge-server to a local MCP host, how
to prepare the synthetic HTTP trial, how to run the HTTP service in the VM,
and what the server can return. The [README](../README.md#quick-start) has
the short version. The [Phase 1 contract](phase-1-contract.md) defines the
exact tool behavior.

For stdio, the MCP host starts the server as a subprocess and talks to it
over stdin and stdout; you do not start it yourself. The separate HTTP
launcher runs a protected loopback service, started by hand or by systemd.
ChatGPT can use it through Cloudflare Access Managed OAuth and Tunnel; the
[Task 7
record](implementation-tasks.md#task-7--http-entry-point-and-synthetic-chatgpt-trial-complete)
lists the trial results and accepted evidence limitations.

## Requirements

- A clone of this repository. The examples use `/path/to/knowledge-server`;
  replace it with the absolute path of your clone.
- uv. It installs Python 3.14 and the locked dependencies when the command
  first runs. The [VM service](#run-the-http-service-in-the-vm) uses the
  system Python instead.
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
loopback. This section runs it by hand in synthetic mode, with the invented
sample notes only; [Run the HTTP service in the
VM](#run-the-http-service-in-the-vm) sets it up as a permanent service. Every
request needs a valid signed Cloudflare assertion for the pinned owner
subject; the opaque OAuth access token alone is not enough. The [HTTP
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
   procedure](web-access.md#owner-identity) requires. Do not paste
   assertions, claims, or credentials into chat. The launcher does not start
   without an owner subject, so no tool can be used before it is pinned.
3. Copy `deploy/config.example.toml` to a private file outside the
   repository, and restrict it to your account:

   ```sh
   install -m 600 /path/to/knowledge-server/deploy/config.example.toml \
     /absolute/private/trial.toml
   ```

   Set `root` to the directory from step 1. Replace `team_domain` and
   `public_host` with your Cloudflare team domain and MCP hostname, and
   replace the audience and owner placeholders with the values from step 2.
   Keep `mode = "synthetic"`.

   `allowed_origins` accepts exact values only. A request without `Origin`
   is accepted; with this empty list, any request that sends `Origin` is
   rejected. The empty list was sufficient for the ChatGPT trial. Add an
   origin only after verifying that the chosen client needs it.
4. From a revision whose local validation and diff review passed, start the
   launcher:

   ```sh
   uv run --project /path/to/knowledge-server --locked \
     knowledge-server-http --config /absolute/private/trial.toml
   ```

   Expected result: the process keeps running and listens only on
   `127.0.0.1:8000`. It does not interpret proxy headers, never treats
   unsigned identity headers as authentication, and ignores `KNOWLEDGE_ROOT`
   and `.env`. Stderr receives one line per request, with only the method,
   status, and latency, and the fixed event lines that the [log
   policy](web-access.md#logging) lists.

   If startup fails, the process exits with status `1` and writes one line,
   `knowledge-server-http startup category=<category>`, to stderr. The [log
   policy](web-access.md#logging) lists the categories; `configuration`
   includes a directory that fails the manifest check.

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
origin implements no OAuth discovery or registration.

## Run the HTTP service in the VM

This runbook runs the HTTP server and cloudflared as systemd services in the
Ubuntu 26.04 VM and keeps a dedicated vault checkout up to date. The [runtime
isolation](web-access.md#runtime-isolation) section explains the design.
Replace each `<placeholder>`.

Install with synthetic mode first; real notes need the [real-vault
mode](web-access.md#launch-modes) and the authorization that Task 9 obtains.

### Install the server

1. Install the packages and create the service account. Ubuntu 26.04
   provides Python 3.14 as `python3.14`:

   ```sh
   sudo apt install git ripgrep python3.14
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin \
     knowledge-server
   ```

2. Clone this repository to `/opt/knowledge-server`, check out a revision
   whose local validation passed, and install it with the system Python:

   ```sh
   sudo install -d -o "$USER" /opt/knowledge-server
   git clone <repository-url> /opt/knowledge-server
   git -C /opt/knowledge-server checkout <commit>
   cd /opt/knowledge-server
   uv sync --locked --no-dev --no-editable --compile-bytecode \
     --link-mode copy --python /usr/bin/python3.14 --no-python-downloads \
     --reinstall-package knowledge-server
   ```

   The installed copy changes only at the next `uv sync`, which reinstalls
   the checked-out source. A Python that uv downloads would be in your home
   directory, which the service cannot read.
3. Create the configuration, readable by root and the service account only:

   ```sh
   sudo install -d -m 0750 -g knowledge-server /etc/knowledge-server
   sudo install -m 0640 -g knowledge-server \
     /opt/knowledge-server/deploy/config.example.toml \
     /etc/knowledge-server/config.toml
   sudoedit /etc/knowledge-server/config.toml
   ```

   Set `root = "/opt/knowledge-server/tests/fixtures/vault"`, the sample
   notes in the installed checkout, and the Access values, as step 3 of the
   [synthetic trial](#prepare-the-synthetic-http-trial) describes. If you
   have the configuration from the synthetic trial, copy its Access values.
   Keep `mode = "synthetic"`.
4. Install and start the unit:

   ```sh
   sudo install -m 0644 /opt/knowledge-server/deploy/knowledge-server.service \
     /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now knowledge-server
   ```

5. Check the service:

   ```sh
   curl -si http://127.0.0.1:8000/mcp
   sudo ss -ltnp 'sport = :8000'
   sudo systemctl restart knowledge-server
   curl -si http://127.0.0.1:8000/mcp
   ```

   Expected results: status 401 with `cache-control: no-store`, before and
   after the restart, and one listener, on `127.0.0.1:8000` only. The 401
   shows only that the listener and gate respond. Right after a start,
   `curl` can fail to connect; retry for up to 10 seconds. If no 401 with
   `no-store` appears, check `systemctl status knowledge-server` and
   `journalctl -u knowledge-server`.

### Install the tunnel connector

Install the connector when the deployment is authorized, in Task 9.

1. In the Cloudflare dashboard, open the tunnel's installation command for
   Debian or Ubuntu and run its `sudo cloudflared service install <token>`
   line. The command saves the token in a file in `/etc/cloudflared` that
   only root can read, and creates a systemd unit that starts cloudflared at
   boot.
2. In the tunnel's published route for the MCP hostname, set the service to
   `http://127.0.0.1:8000` and **HTTP Host Header** to the configured
   `public_host`. Confirm that no Cache Rule or Page Rule covers the MCP
   hostname.

### Set up the vault checkout

The server reads a dedicated checkout, `/srv/knowledge-vault`, that only the
synchronization changes. Do not edit notes in it.

1. Clone the vault remote with your account:

   ```sh
   sudo install -d -o "$USER" /srv/knowledge-vault
   git clone ssh://<nas-user>@<nas-host>/<path-to-vault.git> \
     /srv/knowledge-vault
   ```

   The default file permissions let the service account read the notes,
   and the unit keeps them read-only for the server.
2. Add this line to your crontab with `crontab -e`, to fast-forward the
   checkout every 15 minutes:

   ```text
   */15 * * * * git -C /srv/knowledge-vault pull --ff-only --quiet
   ```

   cron cannot enter a passphrase, so the SSH key for the vault remote must
   work without one. A key that the NAS limits to reading is safer.

The server still serves the synthetic notes. Activating the real-vault mode
for `/srv/knowledge-vault` or one subtree is Task 9: after the vault owner
authorizes the scope, set `mode` and `root` as [launch
modes](web-access.md#launch-modes) defines and restart the server.

### Operate the services

| Action | Command |
|---|---|
| Show status | `systemctl status knowledge-server cloudflared` |
| Show recent server log lines | `journalctl -u knowledge-server -n 50` |
| Stop, start, or restart | `sudo systemctl stop knowledge-server` (or `start`, `restart`; or `cloudflared`) |
| Synchronize now | `git -C /srv/knowledge-vault pull --ff-only` |

**Emergency stop.** From a shell on the VM, through Tailscale SSH or the
Unraid VM console, run:

```sh
sudo systemctl disable --now cloudflared knowledge-server
```

Both services stop and stay stopped after a reboot. Issued OAuth tokens stay
valid until they expire, but they reach nothing while the services are
stopped. This stop depends on access to the VM; [shutdown and
revocation](web-access.md#shutdown-and-revocation) describes the
Cloudflare-side alternative, and Task 9 verifies the emergency stop. To
resume, run `sudo systemctl enable --now knowledge-server cloudflared`.

**Synchronization.** The next tool call sees pulled files; no restart is
needed. A pull fails instead of merging when the remote history has
diverged, for example after a rewrite. The checkout has no local changes, so
reset it with `git -C /srv/knowledge-vault fetch` and
`git -C /srv/knowledge-vault reset --hard '@{upstream}'`.

**Update.** Record the installed revision, then install the new one. If a
command fails, stop there; the running server is unchanged until the
restart.

```sh
git -C /opt/knowledge-server rev-parse HEAD
git -C /opt/knowledge-server fetch
git -C /opt/knowledge-server checkout <commit>
cd /opt/knowledge-server
uv sync --locked --no-dev --no-editable --compile-bytecode \
  --link-mode copy --python /usr/bin/python3.14 --no-python-downloads \
  --reinstall-package knowledge-server
```

If `deploy/knowledge-server.service` changed, install it again with the
first command of step 4 and run `sudo systemctl daemon-reload`. Then run
`sudo systemctl restart knowledge-server` and repeat the `curl` check of
step 5, with the same retry.

**Rollback.** Repeat the update with the recorded revision, including the
unit file and `daemon-reload` if the unit differs. The server keeps no index
or other state, so a rollback needs no data migration.

**Credentials and private settings.** The tunnel token is in a root-only
file in `/etc/cloudflared`. To replace it, rotate it in the dashboard, run
`sudo cloudflared service uninstall`, and then run the new installation
command; `service install` does not replace an existing service. The vault
remote key is in your `~/.ssh`. The Access values in
`/etc/knowledge-server/config.toml` are private identifiers, not
credentials. To change them, edit the file with `sudoedit`, run
`sudo systemctl restart knowledge-server`, and repeat the `curl` check of
[Install the server](#install-the-server) step 5.
No OAuth token is stored in the VM.

**Troubleshooting.** The server log uses the categories that the [log
policy](web-access.md#logging) defines.

| Symptom | Check |
|---|---|
| `startup category=...` | The log policy's table names the cause. `writable-root` means the server was started outside its unit, for example by hand from your account. |
| 401 with `event category=key-fetch-failed` | The VM cannot reach `https://<team>.cloudflareaccess.com`. After a failure, the server waits 30 seconds before it fetches again. |
| 401 with only `event category=assertion-rejected` | `team_domain`, `audience`, or `owner_subject` does not match the Access application, or another identity sent the request. |
| 421 | **HTTP Host Header** in the tunnel route is not `public_host`. |
| Cloudflare cannot reach the origin | The server is stopped, or the route uses `localhost`, which can resolve to IPv6 `::1`; use `127.0.0.1`. |
| `event category=tool-failed` | Repeat the call over stdio with the same root; the stdio log names the exception type and location. |

## What the server reads

- **The live working tree.** The server reads the files in the local vault
  checkout as they are now, including uncommitted and untracked notes. An
  edit is visible in the next tool call without a restart. The server does
  not search Git history or cache content. `KNOWLEDGE_ROOT` is resolved once
  at startup, so restart the host after moving the vault. The HTTP launcher
  instead reads its root from its private configuration file at startup; in
  synthetic mode, keep that directory dedicated to the invented notes.
- **No Git operations.** The server never writes files and never runs Git.
  You synchronize the local vault checkout with Git yourself; in the VM, a
  cron job does it. Results during a
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
provider as part of the conversation. Any visible note under the root can be
returned, so set the root to a directory whose notes you are willing to
send to that provider. The provider's terms and your account settings decide
how long it keeps them. The hosts also save session transcripts locally:
Claude Code under `~/.claude/projects/` and Codex under `~/.codex/sessions/`.
The server itself writes only short diagnostic messages to stderr, never note
contents or queries. In the web route, Cloudflare also handles decrypted
requests and responses, and ChatGPT sends tool results to OpenAI. See the
[web trust boundary](web-access.md#trust-boundary-and-data-handling) before
provisioning or considering real-vault use.

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
