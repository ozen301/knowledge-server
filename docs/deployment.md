# Deployment and operations

This guide sets up and operates the remote route through which ChatGPT uses
knowledge-server, and the daily tasks that keep it running. The [design
decisions](design-decisions.md#why-this-remote-route) explain the route, the
[HTTP contract](http-contract.md) specifies the server's checks, and the
[usage guide](usage.md) covers local MCP hosts over stdio.

```text
ChatGPT (developer-mode connector)
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
| Synchronization | A line in the administrator's crontab |
| Logs | `journalctl -u knowledge-server`, `journalctl -u cloudflared` |
| Cloudflare and ChatGPT settings | The Cloudflare dashboard and ChatGPT's connector settings |

Daily tasks, updates, the emergency stop, and troubleshooting are in
[Operate the services](#operate-the-services).

## Set up

Before you start, the vault owner reads the [trust
boundary](design-decisions.md#trust-boundary-and-data-handling) and
authorizes the exposed scope: the whole vault checkout or one subtree of it.
All visible `.md` notes in that scope become readable through the connector,
including notes that later pulls add, and Cloudflare and OpenAI handle the
excerpts that ChatGPT receives. Replace each `<placeholder>`.

### Prepare Cloudflare

Set up the edge as the [edge
configuration](design-decisions.md#edge-configuration) requires: a
self-hosted Access application with Managed OAuth for the MCP hostname only,
an allow policy for the vault owner's identity only, and a dashboard-managed
tunnel. Use a domain whose DNS Cloudflare manages, in the account that holds
the Zero Trust organization. In the Managed OAuth
settings:

- Add `https://chatgpt.com/connector/oauth/*` to the redirect allowlist.
  Without it, ChatGPT's connection fails with a generic settings rejection.
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

The server reads a dedicated checkout, `/srv/knowledge-vault`, that only the
synchronization changes. Do not edit notes in it.

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
   work without one. A key that the NAS limits to reading is safer.

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
   Settings**, set **HTTP Host Header** to the configured `public_host`;
   [forwarded Host](design-decisions.md#vm-services-and-network) explains why.
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

Both services stop and stay stopped after a reboot. ChatGPT's tool calls
then fail. Issued OAuth tokens stay valid until they expire, but they reach
nothing while the services are stopped. To resume, run
`sudo systemctl enable --now knowledge-server cloudflared`.

### Synchronization

The next tool call sees pulled files; no restart is needed. A pull fails
instead of merging when the remote history has diverged, for example after a
rewrite. The checkout has no local changes, so reset it:

```sh
git -C /srv/knowledge-vault fetch
git -C /srv/knowledge-vault reset --hard '@{upstream}'
```

Results during a pull can mix old and new files; repeat the question
afterwards.

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
for an update. The server keeps no index or other state, so a rollback needs
no data migration. The checkout now stays at that revision; at the next
update, run `git -C /opt/knowledge-server switch main` before the pull.

### Credentials and private settings

- **Tunnel token.** It is in a root-only file in `/etc/cloudflared`. To
  replace it, rotate it in the dashboard, run
  `sudo cloudflared service uninstall`, and then run the new installation
  command; `service install` does not replace an existing service.
- **Vault remote key.** It is in your `~/.ssh`.
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
| `startup category=...` in the server log | The [log policy](http-contract.md#logging) names the cause. `writable-root` means the server was started outside its unit, for example by hand from your account. |
| 401 with `event category=key-fetch-failed` | The VM cannot reach `https://<team>.cloudflareaccess.com`. After a failure, the server waits 30 seconds before it fetches again. |
| 401 with only `event category=assertion-rejected` | `team_domain`, `audience`, or `owner_subject` does not match the Access application, or another identity sent the request. |
| 421 | **HTTP Host Header** in the tunnel route is not `public_host`. |
| 403 | The request sent an `Origin` header that `allowed_origins` does not list. |
| Cloudflare cannot reach the origin | The server is stopped, or the route uses `localhost`, which can resolve to IPv6 `::1`; use `127.0.0.1`. |
| ChatGPT cannot connect, or Cloudflare blocks requests | Check the redirect allowlist in [Prepare Cloudflare](#prepare-cloudflare), and Cloudflare's challenge and security events, as the [edge configuration](design-decisions.md#edge-configuration) describes. |
| `event category=tool-failed` | Repeat the call over stdio with the same root; the stdio log names the exception type and location. |

If none of these applies, check `systemctl status knowledge-server` and the
full `journalctl -u knowledge-server` output.

**Check without real notes.** To tell a route problem from a problem with
the notes, or to share answers and logs while debugging, serve the invented
sample notes from the installed checkout:

1. With `sudoedit /etc/knowledge-server/config.toml`, set
   `mode = "synthetic"` and
   `root = "/opt/knowledge-server/tests/fixtures/vault"`, and run
   `sudo systemctl restart knowledge-server`.
2. Ask ChatGPT: "Which CPU does my NAS use? Cite the note and line."
   Expected result: "AMD Ryzen 5 2600X", citing
   `infrastructure/nas-configuration.md`, line 7.
3. Set `mode` and `root` back to their vault values and restart.
