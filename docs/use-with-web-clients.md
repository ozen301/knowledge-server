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
26.04 VM, the paths in the table below, a vault remote on a Forgejo Git
service reached over HTTPS, and synchronization every 15 minutes. The
shipped unit, `deploy/knowledge-server.service`, fixes the service account,
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
| Server's Git repository for write proposals | `/var/lib/knowledge-server/vault.git`, created by the server |
| Write access to the vault remote (mounted share only) | The unit drop-in `/etc/systemd/system/knowledge-server.service.d/vault-remote.conf` |
| SSH key for write proposals (SSH only, private) | `/etc/knowledge-server/vault-remote-key`, owned by the service account, and the NAS host key in `/etc/knowledge-server/known_hosts` |
| Access token for write proposals (HTTPS only, private) | `/etc/knowledge-server/vault-remote-credentials`, owned by the service account |
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

1. Install Git and clone the vault remote with your account, with its SSH
   or HTTPS URL:

   ```sh
   sudo apt install git
   sudo install -d -o "$USER" /srv/knowledge-vault
   git clone <vault-remote-url> /srv/knowledge-vault
   ```

   The default file permissions let the service account read the notes,
   and the unit keeps them read-only for the server.
2. Add this line to your crontab with `crontab -e`, to fast-forward the
   checkout every 15 minutes:

   ```text
   */15 * * * * git -C /srv/knowledge-vault pull --ff-only --quiet
   ```

   cron cannot enter a passphrase or password, so your account must reach
   the vault remote without one: with an SSH key without a passphrase, or,
   over HTTPS, with a credential that Git stores for your account. Access
   limited to reading is safer. [Write proposals](#set-up-write-proposals)
   use separate credentials.

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

### Set up write proposals

Write proposals let the client save new notes and edits as proposals,
branches of the vault remote, which you then [review and
merge](#review-proposals) in any clone; the server never changes the notes
themselves. The server creates the commits in a Git repository of its own,
`/var/lib/knowledge-server/vault.git`, which it creates at startup, and
pushes them to the vault remote.

The server reaches the vault remote in one of three ways. If the VM mounts
the NAS share that holds the vault remote, the server can push to its path,
and needs no credentials and no network access to the NAS. Otherwise it
pushes over SSH with a key of its own, or over HTTPS, as to a Git service
such as Forgejo, with an access token of its own. Give the server access as
one of the next three sections describes, and then [configure write
proposals](#configure-write-proposals).

#### Vault remote on a mounted share

`<vault-remote-path>` is the absolute path of the vault remote's bare
repository on the VM, such as `/mnt/nas/git/knowledge-vault.git`. Neither
the path nor the target of a symlink in it may be under `/home`, `/root`,
`/run/user`, `/tmp`, or `/var/tmp`, which the unit hides from the service.

1. Let the service write in the repository, with a drop-in for the unit:

   ```sh
   sudo install -d /etc/systemd/system/knowledge-server.service.d
   printf '[Service]\nReadWritePaths=-<vault-remote-path>\n' |
     sudo tee /etc/systemd/system/knowledge-server.service.d/vault-remote.conf
   sudo systemctl daemon-reload
   ```

   The unit keeps the rest of the file system read-only. The leading `-`
   lets the server start, and serve reads, while the share is not mounted.
2. Check that the service account can write in every directory of the
   repository:

   ```sh
   sudo -u knowledge-server find <vault-remote-path> -type d ! -writable
   ```

   Expected result: no output. A listed directory means that the file
   permissions do not let the service account write there. A repository
   whose directories every account can write in, and whose
   `core.sharedRepository` is `0777`, keeps new files writable for the
   other accounts that push to it.

#### Vault remote over SSH

1. Create the server's SSH key. It has no passphrase, because the service
   cannot enter one:

   ```sh
   sudo ssh-keygen -t ed25519 -N '' -C knowledge-server \
     -f /etc/knowledge-server/vault-remote-key
   sudo chown knowledge-server: /etc/knowledge-server/vault-remote-key
   ```

   Give the public key, `/etc/knowledge-server/vault-remote-key.pub`, push
   access to the vault remote on the NAS. The firewall must allow SSH from
   the VM to the NAS.
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
   [Connect ChatGPT](#connect-chatgpt) shows that it can push.

#### Vault remote over HTTPS

The steps use Forgejo; other Git services issue similar access tokens. The
firewall must allow HTTPS from the VM to the Git service, and the service's
certificate must be valid for the VM's trusted certificates.

1. Create an access token for the server. In Forgejo, open **Settings ->
   Applications**, generate a token with read and write access to
   repositories, and copy it. The token can push to every repository of
   your account.
2. Store your user name on the Git service and the token in a file that
   only the service account can read:

   ```sh
   sudo install -m 600 -o knowledge-server -g knowledge-server /dev/null \
     /etc/knowledge-server/vault-remote-credentials
   sudoedit /etc/knowledge-server/vault-remote-credentials
   ```

   Enter two lines, in Git's credential format:

   ```text
   username=<git-user>
   password=<token>
   ```

   `sudoedit` keeps the file's owner and mode. Do not give the token on a
   command line, where the shell history can keep it.
3. Check that the service account can reach the vault remote with the file
   and the Git settings that the server uses:

   ```sh
   sudo -u knowledge-server env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null GIT_TERMINAL_PROMPT=0 \
     git -C / -c http.followRedirects=false \
     -c 'credential.https://<git-host>/<path-to-vault.git>.helper=!f() { test "$1" = get && cat /etc/knowledge-server/vault-remote-credentials; }; f' \
     ls-remote https://<git-host>/<path-to-vault.git> main
   ```

   Use the same URL in both places. Ubuntu's default `sudo` settings keep your
   environment, such as `SSH_ASKPASS`, out of the command, as the server keeps
   it out of Git. Expected result: one line with a commit ID and
   `refs/heads/main`. An error means that the file, the token, or the URL is
   wrong; a redirect, for example after a repository rename, also fails, so
   use the URL that the redirect names. The check shows only that the server
   can read the vault remote; the test note in [Connect
   ChatGPT](#connect-chatgpt) shows that it can push.

#### Configure write proposals

Add one of these lines to `/etc/knowledge-server/config.toml` with
`sudoedit`, for a mounted share, SSH, or HTTPS:

```toml
vault_remote = "<vault-remote-path>"
vault_remote = "ssh://<nas-user>@<nas-host>/<path-to-vault.git>"
vault_remote = "https://<git-host>/<path-to-vault.git>"
```

For SSH, use the URL that you checked, in the `ssh://` form; the server
rejects the short form `<nas-user>@<nas-host>:<path>`. For HTTPS, use the
URL that you checked, without a user name; with one, Git does not use the
credential file, so the server rejects it. Run
`sudo systemctl restart knowledge-server`, and repeat the `curl` check of
[Install the server](#install-the-server) step 5. If it fails, look for a
`startup category=...` line in `journalctl -u knowledge-server -n 20`, and
see [Troubleshooting](#troubleshooting). systemd restarts a failed server
every few seconds, so the unit can look active while it fails.

To disable writing, remove the `vault_remote` line, and the drop-in if you
added one, run `sudo systemctl daemon-reload`, and restart the server. You
can then delete `/var/lib/knowledge-server/vault.git`.

The server finds proposals only on the configured vault remote. If you move
the vault remote, push the open `proposal/*` branches from the old one to
the new one before you change `vault_remote`.

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

   ChatGPT uses the connector's description to decide when to call the
   tools. This example tells it when to search, how literal search works,
   how proposals behave, and where the note conventions are. Adapt it to
   your notes.

   ```text
   Access to my personal Markdown notes. Use it when I ask about my notes, or about something I may have written down. Search is literal text matching, not semantic: try specific words, synonyms, and both English and Japanese terms. You can also save new notes and propose edits, but only as proposals that I review before they change my notes. Save a new note when I ask you to keep something. Propose an edit only when I ask for one and agree to the change. Before you save a note or propose an edit, read AGENTS.md at the root of the vault and follow its note conventions. Search and knowledge_list show only my notes, not unmerged proposals; to see proposals, use knowledge_list_proposals, and pass a proposal to knowledge_read. To change something you proposed earlier, revise its proposal instead of starting a new one.
   ```

2. Sign in as the vault owner when Cloudflare asks.
3. In a chat with the connector enabled, ask a question that a known note in
   the scope answers, and ask for a citation. Expected result: the right
   answer, citing that note and its lines, and request lines with
   `status=200` in `journalctl -u knowledge-server`. Then ask ChatGPT to save
   a short test note; it may write without asking for confirmation, because
   you asked for the write. Expected result: the name of a new proposal
   branch. Accept or reject the test note as [Review
   proposals](#review-proposals) describes.

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
- **The server's vault remote key,** for write proposals over SSH. It is in
  `/etc/knowledge-server/vault-remote-key`, readable only by the service
  account. To revoke it, remove its public key from the NAS.
- **The server's access token,** for write proposals over HTTPS. It is in
  `/etc/knowledge-server/vault-remote-credentials`, readable only by the
  service account. To rotate it, create a new token, replace the
  `password=` line with `sudoedit`, and delete the old token in the Git
  service. The server reads the file for each fetch and push, so it needs no
  restart. To revoke the token, delete it in the Git service.
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
| `startup category=...` in the server log | The [log policy](http-contract.md#logging) names the cause. `writable-root` means the server was started outside its unit, for example by hand from your account. `configuration` right after you enable write proposals means that `vault_remote` is not an absolute path, an `ssh://` URL, or an `https://` URL in the [allowed form](http-contract.md#launch-configuration), or that the installed version is older than proposal branches; [update the server](#update-the-server). `vault-remote` means that the server could not prepare its own Git repository: check that the installed unit has `StateDirectory=knowledge-server` and that the service account can read the vault checkout, including its `.git` directory. |
| A proposal tool returns `GIT_FAILED` | For a [mounted share](#vault-remote-on-a-mounted-share), check that it is mounted, and repeat the check of step 2. For [SSH](#vault-remote-over-ssh), repeat the check of step 3, and check that the NAS gives the key push access. For [HTTPS](#vault-remote-over-https), repeat the check of step 3, and check that the token still exists and can write to repositories. If the access is right, the server's own repository may be damaged, for example after a full disk. Run `sudo systemctl stop knowledge-server`, `sudo rm -r /var/lib/knowledge-server`, and `sudo systemctl start knowledge-server`; the start rebuilds the repository. Proposals that were pushed are on the vault remote and stay unchanged. |
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
