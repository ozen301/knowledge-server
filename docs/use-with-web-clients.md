# Use with web clients

This guide sets up and operates the remote route through which web clients,
ChatGPT and Claude.ai, use knowledge-server: the services in the VM, the
connectors, write proposals and their review, and the daily tasks that keep
it running. The [design decisions](design-decisions.md#why-this-remote-route)
explain the route, and the [HTTP contract](http-contract.md) specifies the
server's checks. The [guide for local MCP hosts](use-with-local-hosts.md)
covers stdio; its section on [how the tools
behave](use-with-local-hosts.md#how-the-tools-behave) applies to web clients
too.

```text
ChatGPT or Claude.ai (via custom connector)
    -> Cloudflare Access (vault owner sign-in with Managed OAuth)
    -> Cloudflare Tunnel
    -> cloudflared service in the Ubuntu VM
    -> knowledge-server-http service on 127.0.0.1:8000 in the same VM
    -> /srv/knowledge-vault, a dedicated vault checkout
```

This guide follows the vault owner's own deployment as an example: an Ubuntu
26.04 VM, the paths in the table below, a vault remote reached over SSH, and
synchronization every 15 minutes. The shipped unit,
`deploy/knowledge-server.service`, fixes the service account,
`knowledge-server`, the install path, `/opt/knowledge-server`, and the
configuration path, `/etc/knowledge-server/config.toml`; keep them unless you
also edit the unit. Choose your own vault checkout path, port, synchronization
schedule, and administration access, and adjust the commands to match. If you
change `port`, use it in the tunnel route and the `curl` checks. On another
distribution, also adjust the package installation, the Python path, and, if
`rg` is not in `/usr/bin`, the unit's `PATH`.

## Where things are in the example deployment

| Item | Location |
|---|---|
| Server checkout, tracking `main`, and its virtual environment | `/opt/knowledge-server`, `/opt/knowledge-server/.venv` |
| Server configuration (private) | `/etc/knowledge-server/config.toml` |
| Server unit | `/etc/systemd/system/knowledge-server.service`, from `deploy/` |
| cloudflared unit and tunnel token | Created by `cloudflared service install`; the token is in a root-only file in `/etc/cloudflared` |
| Vault checkout | `/srv/knowledge-vault`, owned by the administrator's account |
| Server's Git repository for write proposals (optional) | `/var/lib/knowledge-server/vault.git`, created by the server |
| SSH key for write proposals (optional, private) | `/etc/knowledge-server/vault-remote-key`, owned by the service account, and the NAS host key in `/etc/knowledge-server/known_hosts` |
| Synchronization | A line in the administrator's crontab |
| Logs | `journalctl -u knowledge-server`, `journalctl -u cloudflared` |
| Cloudflare and client settings | The Cloudflare dashboard, and the connector settings in ChatGPT and Claude.ai |

Daily tasks, updates, the emergency stop, and troubleshooting are in
[Operate the services](#operate-the-services).

## Set up

Before you start, the vault owner reads the [trust
boundary](design-decisions.md#trust-boundary-and-data-handling), reviews the
client account's training, retention, and admin access, and authorizes the
exposed scope: the whole vault checkout or one subtree of it. All visible
`.md` notes in that scope become readable through the connector, including
notes that later pulls add, and Cloudflare and the client's provider, OpenAI
or Anthropic, handle the excerpts that the client receives. For a ChatGPT
Business workspace, OpenAI's pages conflict on whether workspace admins can
read members' conversations; treat chats that contain note excerpts as
possibly visible to them. Repeat the review after a change of client account
or plan.

The VM needs outbound access to Cloudflare for cloudflared, and to
`https://<team>.cloudflareaccess.com` for the server's [signing-key
fetch](http-contract.md#assertion-validation). The unit does not restrict the
network; a firewall must allow both. Replace each `<placeholder>`.

### Prepare Cloudflare

Set up the edge as the [edge
configuration](design-decisions.md#edge-configuration) requires: a
self-hosted Access application with Managed OAuth for the MCP hostname only,
an allow policy for the vault owner's identity only, and a dashboard-managed
tunnel. Use a domain whose DNS Cloudflare manages, in the account that holds
the Zero Trust organization. In the Managed OAuth
settings:

- For ChatGPT, add `https://chatgpt.com/connector/oauth/*` to the redirect
  allowlist. For Claude.ai, add `https://claude.ai/api/mcp/auth_callback`.
  Without the right redirect allowlist, the client cannot complete the OAuth
  flow.
- Keep **Allow localhost clients** and **Allow loopback clients** disabled.
  They govern OAuth callbacks, not the server's loopback listener.
- Keep the default access token lifetime of 15 minutes.

Record these values for the server configuration:

| Value | Source |
|---|---|
| Team domain, `<team>.cloudflareaccess.com` | The Zero Trust organization |
| Application AUD tag | The Access application |
| MCP hostname | The hostname that the Access application covers |
| Owner subject | The vault owner's `sub` claim, established privately as [owner identity](http-contract.md#owner-identity) requires |

Do not paste assertions, claims, or credentials into a chat. Add the tunnel's
published route only after the server runs with the owner subject.

### Set up the vault checkout

The server reads a dedicated checkout, `/srv/knowledge-vault`. Only the
synchronization changes it; do not edit or commit in it.

1. Install Git and clone the vault remote with your account:

   ```sh
   sudo apt install git
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
   work without one. A key that the NAS limits to reading is safer. [Write
   proposals](#enable-write-proposals) use a separate key.

### Install the server

1. Install the packages and create the service account. Ubuntu 26.04
   provides Python 3.14 as `python3.14`:

   ```sh
   sudo apt install ripgrep python3.14
   sudo useradd --system --no-create-home --shell /usr/sbin/nologin \
     knowledge-server
   ```

   Install uv for your account as its [installation
   guide](https://docs.astral.sh/uv/getting-started/installation/) describes.
   Only the installation steps use it; the service does not.

2. Clone the `main` branch of this repository to `/opt/knowledge-server` and
   install it with the system Python:

   ```sh
   sudo install -d -o "$USER" /opt/knowledge-server
   git clone --branch main <repository-url> /opt/knowledge-server
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

   Set `mode = "vault"` and `root = "/srv/knowledge-vault"`, or the absolute
   path of the authorized subtree. Set `team_domain`, `audience`,
   `owner_subject`, and `public_host` to the values from [Prepare
   Cloudflare](#prepare-cloudflare). Keep `allowed_origins = []`: ChatGPT's
   requests reach the server without an `Origin` header, and with an empty
   list any request that sends one is rejected. The [launch
   configuration](http-contract.md#launch-configuration) defines each field.
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
   `no-store` appears, see [Troubleshooting](#troubleshooting).

### Install the tunnel connector

1. In the Cloudflare dashboard, open the tunnel's installation commands for
   Debian or Ubuntu and run them: they install the `cloudflared` package and
   then run `sudo cloudflared service install <token>`. That command saves the
   token in a file in `/etc/cloudflared` that only root can read, and creates
   a systemd unit that starts cloudflared at boot.
2. In the tunnel's published route for the MCP hostname, set the service to
   `http://127.0.0.1:8000`. Under **Additional application settings -> HTTP
   Settings**, set **HTTP Host Header** to the configured `public_host`.
   Cloudflare does not document which Host cloudflared sends when this
   setting is empty, and the server rejects any other Host. Use `127.0.0.1`,
   not `localhost`, which can resolve to IPv6 `::1`, where the server does
   not listen.
   Confirm that no Cache Rule or Page Rule covers the MCP hostname.
3. Check that cloudflared received the route:

   ```sh
   journalctl -u cloudflared | grep 'Updated to new configuration' | tail -1
   ```

   Expected result: the route entry contains
   `"service":"http://127.0.0.1:8000"` and `"httpHostHeader"` with the value
   of `public_host`. Then check the public endpoint:

   ```sh
   curl -si https://<mcp-hostname>/mcp
   ```

   Expected result: status 401 from Cloudflare with a `www-authenticate`
   header that names the protected-resource metadata.

### Connect ChatGPT

1. In ChatGPT's developer mode, create a connector with OAuth
   authentication and the MCP server URL `https://<mcp-hostname>/mcp`. The
   URL must end in `/mcp`: it is the only path that serves MCP and the
   resource that the protected-resource metadata advertises. After
   authorization, the server answers every other path, including `/`, with
   404. ChatGPT registers itself with dynamic client registration.
2. Sign in as the vault owner when Cloudflare asks.
3. In a chat with the connector enabled, ask a question that a known note in
   the scope answers, and ask for a citation. Expected result: the right
   answer, citing that note and its lines, and request lines with
   `status=200` in `journalctl -u knowledge-server`.

### Connect Claude.ai

1. In Claude.ai, open Customize -> Connectors and select Add custom
   connector.
2. Enter the MCP server URL `https://<mcp-hostname>/mcp`, and choose OAuth
   sign-in with Register automatically (dynamic client registration).
3. Select Connect and sign in as the vault owner when Cloudflare asks.
4. Check the connection as in step 3 of [Connect ChatGPT](#connect-chatgpt),
   with the connector turned on in the chat's + -> Connectors menu.

Claude.ai chooses when to call the tools from their names and descriptions.
To make it search the notes, mention them in the question or in your
Claude.ai instructions.

### Enable write proposals

This step is optional. It lets the client save new notes and edits as
proposals, branches of the vault remote, which you then [review and
merge](#review-proposals) in any clone; the server never changes the notes
themselves. The server creates the commits in a Git repository of its own,
`/var/lib/knowledge-server/vault.git`, which it creates at startup, and
pushes them with its own SSH key. The firewall must allow SSH from the VM to
the NAS.

Write proposals need a server version that has proposal branches. If the
installed version is older, [update the server](#update-the-server) first:
an older version rejects `vault_remote` and does not start.

1. Create the server's SSH key. It has no passphrase, because the service
   cannot enter one:

   ```sh
   sudo ssh-keygen -t ed25519 -N '' -C knowledge-server \
     -f /etc/knowledge-server/vault-remote-key
   sudo chown knowledge-server: /etc/knowledge-server/vault-remote-key
   ```

   Give the public key, `/etc/knowledge-server/vault-remote-key.pub`, push
   access to the vault remote on the NAS.
2. Copy the NAS host key that your account already trusts, so that the
   server can check the NAS without asking:

   ```sh
   ssh-keygen -F <nas-host> | sudo tee /etc/knowledge-server/known_hosts
   ```

   If the NAS uses an SSH port other than 22, write the host as
   `'[<nas-host>]:<port>'`. Expected result: at least one line with a key.
   If there is none, connect to the NAS once with
   `ssh <nas-user>@<nas-host>`, adding `-p <port>` for a port other than
   22, check its fingerprint, and repeat.
3. Check that the service account can reach the vault remote with the key,
   the host key, and the SSH options that the server uses:

   ```sh
   sudo -u knowledge-server env GIT_SSH_COMMAND='ssh -i /etc/knowledge-server/vault-remote-key -o IdentitiesOnly=yes -o BatchMode=yes -o UserKnownHostsFile=/etc/knowledge-server/known_hosts' \
     git -C / ls-remote ssh://<nas-user>@<nas-host>/<path-to-vault.git> main
   ```

   Expected result: one line with a commit ID and `refs/heads/main`. An
   error means that the key, the host key, or the URL is wrong. The check
   shows only that the server can read the vault remote; the test note in
   step 5 shows that it can push.
4. Add this line to `/etc/knowledge-server/config.toml` with `sudoedit`,
   with the URL of step 3:

   ```toml
   vault_remote = "ssh://<nas-user>@<nas-host>/<path-to-vault.git>"
   ```

   The server accepts only this `ssh://` form, not the short form
   `<nas-user>@<nas-host>:<path>`. Run
   `sudo systemctl restart knowledge-server`, and repeat the `curl` check of
   [Install the server](#install-the-server) step 5. If it fails, look for a
   `startup category=...` line in `journalctl -u knowledge-server -n 20`,
   and see [Troubleshooting](#troubleshooting). systemd restarts a failed
   server every few seconds, so the unit can look active while it fails.
5. In ChatGPT or Claude.ai, check that the connector lists
   `knowledge_propose_note`, `knowledge_propose_edit`, and
   `knowledge_list_proposals`; if it does not, refresh the connector. Ask the
   client to save a short test note; it may write without asking for
   confirmation, because you asked for the write. Then accept or reject the
   test note as [Review proposals](#review-proposals) describes.

To disable writing, remove the `vault_remote` line and restart the server.
You can then delete `/var/lib/knowledge-server/vault.git`.

## Operate the services

| Action | Command |
|---|---|
| Show status | `systemctl status knowledge-server cloudflared` |
| Show recent server log lines | `journalctl -u knowledge-server -n 50` |
| Stop, start, or restart | `sudo systemctl stop knowledge-server` (or `start`, `restart`; or `cloudflared`) |
| Synchronize now | `git -C /srv/knowledge-vault pull --ff-only` |

The server log has one line per request, with the method, status, and
latency, and the fixed startup and event lines that the [log
policy](http-contract.md#logging) lists. It never contains note text, queries,
tokens, or paths. Both services start at boot, and systemd restarts the
server after a failure.

### Emergency stop

From a shell on the VM, run:

```sh
sudo systemctl disable --now cloudflared knowledge-server
```

Both services stop and stay stopped after a reboot. Remote tool calls
then fail. Issued OAuth tokens stay valid until they expire, but they reach
nothing while the services are stopped. To resume, run
`sudo systemctl enable --now knowledge-server cloudflared`.

### Synchronization

The next tool call sees pulled files; no restart is needed. A pull fails
instead of merging when the remote history has diverged, for example after a
rewrite. Then reset the checkout:

```sh
git -C /srv/knowledge-vault fetch
git -C /srv/knowledge-vault reset --hard '@{upstream}'
```

**Warning:** The reset discards commits and changes that exist only in the
checkout. Push them first, or make them again after the reset.

### Review proposals

Agents save each proposal as a branch of the vault remote, named
`proposal/<topic>-<suffix>`; one proposal can change several notes. Review
proposals in any clone of the vault remote, with Git in a terminal or an
editor's Git view. The VM and the server take no part in the review. The
commands use the example branch `proposal/sleep-notes-cleanup-3f9a`; replace
it with the branch that you review.

1. Update `main` and list the proposals:

   ```sh
   git switch main
   git pull --ff-only --prune
   git branch -r --list 'origin/proposal/*'
   ```

   To see the agent's summary of each change in a proposal, run
   `git log main..origin/proposal/sleep-notes-cleanup-3f9a`. Each commit
   message is `Unreviewed proposal:` followed by that summary.

2. Merge the proposal without committing it:

   ```sh
   git merge --no-ff --no-commit origin/proposal/sleep-notes-cleanup-3f9a
   ```

   The proposed changes are now staged. Review them, for example with
   `git diff --cached` or in the editor's Git view, edit the notes as
   necessary, and stage your edits with `git add`. Resolve conflicts as in
   any merge. To reject the proposal, run `git merge --abort` and continue
   with step 4.
3. Commit the merge and push it:

   ```sh
   git commit
   git push
   ```

   If the push is refused because `main` has new commits, run
   `git pull --no-rebase` and push again. A rebase would drop your merge
   commit and replay the proposal's own commits. The next synchronization
   brings the merged notes to the local vault checkout.
4. Delete the branch, after a merge or to reject the proposal:

   ```sh
   git push origin --delete proposal/sleep-notes-cleanup-3f9a
   ```

   An agent can revise a branch while you review it, and the deletion
   discards such a revision. Before you delete a merged branch, you can
   check with `git fetch` and
   `git log --oneline main..origin/proposal/sleep-notes-cleanup-3f9a`: no
   output means that `main` has every commit of the branch.

The deleted branch no longer appears as a proposal, but its text stays in
the history of the vault remote, its backups, and the clones that fetched
it.

### Update the server

1. Record the installed revision, pull `main`, show the changes to the
   deployment files, and install the new revision:

   ```sh
   git -C /opt/knowledge-server rev-parse HEAD
   git -C /opt/knowledge-server pull --ff-only
   git -C /opt/knowledge-server diff <recorded-revision> -- deploy/
   cd /opt/knowledge-server
   uv sync --locked --no-dev --no-editable --compile-bytecode \
     --link-mode copy --python /usr/bin/python3.14 --no-python-downloads \
     --reinstall-package knowledge-server
   ```

   If a command fails, stop there; the running server is unchanged until the
   restart.
2. If `deploy/knowledge-server.service` changed, install it again with the
   first command of [Install the server](#install-the-server) step 4 and run
   `sudo systemctl daemon-reload`. If `deploy/config.example.toml` changed,
   apply the change that your setup needs to
   `/etc/knowledge-server/config.toml` with `sudoedit`.
3. Run `sudo systemctl restart knowledge-server` and repeat the `curl` check
   of [Install the server](#install-the-server) step 5, with the same retry.

**Rollback.** Check out the recorded revision, then run the `uv sync` command
of step 1:

```sh
git -C /opt/knowledge-server checkout <recorded-revision>
```

If the update changed the unit file, install it again from the checked-out
revision and run `sudo systemctl daemon-reload`; if you changed the
configuration for the update, undo that change. Then restart and check as
for an update. The server keeps no index, and its Git repository for
proposals holds nothing that the vault remote lacks, so a rollback needs no
data migration. The checkout now stays at that revision; at the next
update, run `git -C /opt/knowledge-server switch main` before the pull.

### Credentials and private settings

- **Tunnel token.** It is in a root-only file in `/etc/cloudflared`. To
  replace it, rotate it in the dashboard, run
  `sudo cloudflared service uninstall`, and then run the new installation
  command; `service install` does not replace an existing service.
- **Your vault remote key.** The synchronization uses it; it is in your
  `~/.ssh`.
- **The server's vault remote key.** It is in
  `/etc/knowledge-server/vault-remote-key`, readable only by the service
  account. To revoke it, remove its public key from the NAS.
- **Access values.** The values in `/etc/knowledge-server/config.toml` are
  private identifiers, not credentials. To change them, edit the file with
  `sudoedit`, restart the server, and repeat the `curl` check of [Install the
  server](#install-the-server) step 5. If the vault owner is removed and
  added again in Cloudflare, the owner subject can change and must be
  established again.

No OAuth token is stored in the VM.

### Troubleshooting

| Symptom | Check |
|---|---|
| `startup category=...` in the server log | The [log policy](http-contract.md#logging) names the cause. `writable-root` means the server was started outside its unit, for example by hand from your account. `configuration` right after you enable write proposals means that `vault_remote` is not in the `ssh://` form, or that the installed version is older than proposal branches; [update the server](#update-the-server). `vault-remote` means that the server could not prepare its own Git repository: check that the installed unit has `StateDirectory=knowledge-server` and that the service account can read the vault checkout, including its `.git` directory. |
| A proposal tool returns `GIT_FAILED` | Repeat the check of [Enable write proposals](#enable-write-proposals) step 3, and check that the NAS gives the key push access. If both are right, the server's own repository may be damaged, for example after a full disk. Run `sudo systemctl stop knowledge-server`, `sudo rm -r /var/lib/knowledge-server`, and `sudo systemctl start knowledge-server`; the start rebuilds the repository. Proposals that were pushed are on the vault remote and stay unchanged. |
| 401 with `event category=key-fetch-failed` | The VM cannot reach `https://<team>.cloudflareaccess.com`. After a failure, the server waits 30 seconds before it fetches again. |
| 401 with only `event category=assertion-rejected` | `team_domain`, `audience`, or `owner_subject` does not match the Access application, or another identity sent the request. |
| 421 | **HTTP Host Header** in the tunnel route is not `public_host`. |
| 403 | The request sent an `Origin` header that `allowed_origins` does not list. |
| Cloudflare cannot reach the origin | The server is stopped, or the route uses `localhost`, which can resolve to IPv6 `::1`; use `127.0.0.1`. |
| A client cannot connect, or Cloudflare blocks requests | Check the redirect allowlist in [Prepare Cloudflare](#prepare-cloudflare), and Cloudflare's challenge and security events, as the [edge configuration](design-decisions.md#edge-configuration) describes. |
| `event category=tool-failed` | Repeat the call over stdio with the same root; the stdio log names the exception type and location. |

If none of these applies, check `systemctl status knowledge-server` and the
full `journalctl -u knowledge-server` output.

**Check without real notes.** To tell a route problem from a problem with
the notes, or to share answers and logs while debugging, serve the invented
sample notes from the installed checkout:

1. With `sudoedit /etc/knowledge-server/config.toml`, set
   `mode = "synthetic"` and
   `root = "/opt/knowledge-server/tests/fixtures/vault"`, comment out any
   `vault_remote` line, which only vault mode accepts, and run
   `sudo systemctl restart knowledge-server`.
2. Ask the client: "Which CPU does my NAS use? Cite the note and line."
   Expected result: "AMD Ryzen 5 2600X", citing
   `infrastructure/nas-configuration.md`, line 7.
3. Set `mode` and `root` back to their vault values, restore any
   `vault_remote` line, and restart.
