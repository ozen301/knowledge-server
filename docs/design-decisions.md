# Design decisions

Status: agreed design, updated 2026-10-07. This document explains why
knowledge-server works as it does: its goal, the decisions to preserve, the
remote route for ChatGPT, and its trust boundary. The [architecture
overview](architecture.md) describes the components, the [tool
contract](tool-contract.md) and the [HTTP contract](http-contract.md) specify
exact behavior, and the [roadmap](roadmap.md) lists optional later work.

## Goal

Build a small MCP server that lets an agent find an existing note, read the
relevant lines, and cite its vault-relative path. Make that workflow useful
before adding indexing or document conversion.

The existing Markdown files remain authoritative. Any future database,
extracted text, or embedding index must be rebuildable from original sources.

## Scope and context

This is a personal project. The vault owner is its only user and maintains it
by hand, and availability is best effort: if the service stops, the owner
restarts it. These assumptions keep operations simple. They do not weaken the
note-access boundaries, which stay strict for every transport.

The server exposes four read tools over stdio and a protected HTTP route. The
HTTP route can also serve two write tools that save proposals. The server reads
the local vault checkout, configured through `KNOWLEDGE_ROOT` for stdio or the
configuration file for HTTP; the vault remote is used for synchronization and
is not searchable.

The remote route needs one Linux host with systemd, here called the VM, that
runs the server and cloudflared as services. The documents use the vault
owner's own deployment as the **example deployment**: an Ubuntu 26.04 VM on
an Unraid host, administered over Tailscale, with the vault remote on a NAS
and a ChatGPT Business workspace as the client. Where a section names these
settings, the client account review, the authorized scope, or observed
results, it describes that example. Another deployment makes its own choices
and repeats the reviews and checks.

The server is one Python package that runs as one process: an MCP adapter
over an MCP-free knowledge core. Use ordinary typed functions and data models;
avoid a plugin framework, dependency-injection container, or generic database
abstraction before a second backend exists.

## Decisions to preserve

1. **Read the local vault checkout.** Uncommitted and untracked eligible notes
   are visible. Git history is not searched, and synchronization remains a task
   for the vault owner, outside request handling.
2. **Use one visibility policy for every tool.** Hidden paths, symlinks, and
   unsupported types cannot become visible through another tool. Git ignore
   rules are not an authorization system.
3. **Start with literal search.** `ECC Ryzen` means that literal phrase, not
   semantic similarity or an implicit AND query. Future search modes must not
   silently change this behavior.
4. **Make responses bounded and citable.** Return logical paths, line numbers,
   explicit truncation, and useful errors. Never expose server paths through
   raw exceptions.
5. **Keep stored text inert.** Notes are retrieved data, never executable
   instructions. The service does not execute embedded code or automatically
   fetch Markdown URLs.
6. **Keep operational state separate.** Future indexes and caches live outside
   both the vault and the source repository. The inbox and its base copies
   are not operational state but pending note content, which cannot be
   rebuilt. They stay in the checkout so that the read tools serve proposals
   under the same path policy. Git ignores the inbox, and a proposal is not
   part of the knowledge vault until the vault owner merges it.
7. **Use ripgrep for initial literal search.** Its integration must use the
   shared file policy, bounded subprocess output, deadlines, and cancellation.
   ripgrep is the current implementation choice, not a permanent requirement;
   [existing implementation choices](#existing-implementation-choices) states
   when to reconsider it.
8. **Writes are proposals that the vault owner reviews.** The service never
   changes a canonical note. Over HTTP, an agent can save a new note or an
   edit as a [proposal](tool-contract.md#write-proposals) in the inbox; it
   takes effect only when the vault owner merges it in the checkout. Local
   hosts can edit the vault directly, so stdio stays read-only. The service
   unit, not only the path policy, limits the service's writes to the inbox.

The design assumes that the vault owner controls the local vault checkout and
that concurrent edits are trusted. Path checks and symlink rejection protect
the tool boundary, but they do not isolate the service from a hostile local
process running as the same OS user. Stronger isolation requires a restricted
process with appropriately restricted filesystem permissions.

## Existing implementation choices

Keep the implemented tools, safeguards, and tests. Some parts are heavier than
a personal stdio tool needs, but they are tested, and the path checks, bounded
loader, request gate, and assertion check protect the vault now that it is
reachable over HTTP. Reconsider two choices only when their trigger occurs:

- **ripgrep matching.** Search loads and checks every note in its scope in
  Python and uses ripgrep only to match the loaded text. Consider an
  in-process matcher only when NFC or performance work justifies changing the
  match stage. A replacement must keep the agreed case behavior, citations,
  budgets, cancellation, and skipped counts. It is not known whether Python
  matching can reproduce ripgrep's case-insensitive behavior exactly.
- **Internal SDK classes.** To keep strict argument validation, the adapter
  builds tools from SDK classes that the SDK does not export, as the
  [architecture overview](architecture.md#mcp-adapter) explains. Revisit the
  public registration APIs at the next related adapter change or SDK upgrade,
  without weakening validation.

The synthetic HTTP mode and its manifest guard remain beside the real-vault
mode. They serve as a troubleshooting and regression check with invented
notes.

## Why this remote route

Decided on 2026-10-02. ChatGPT is the first web client. It connects to a public
HTTPS endpoint on a Cloudflare-managed domain. Cloudflare Access, with Managed
OAuth and an owner-only policy, signs the vault owner in; Cloudflare Tunnel
then forwards each request, with a signed assertion, to an HTTP entry point in
the adapter layer. The core stays MCP-free, and stdio and the four tool
contracts stay unchanged. The [HTTP contract](http-contract.md) specifies the
entry point.

Reasons for this route:

- OpenAI's Secure MCP Tunnel required an account association that could not
  be established. This route does not depend on it.
- Cloudflare acts as the OAuth authorization server, so the project
  implements no OAuth server. ChatGPT's documented OAuth requirements for
  MCP servers ([OpenAI's authentication
  guide](https://developers.openai.com/plugins/build/auth)) are the
  compatibility target, and the deployment works with them.
- cloudflared connects outbound, so no inbound router port forwarding is
  needed. Administration stays on a private network, Tailscale in the
  example deployment.
- The origin also validates Cloudflare's signed assertion and the pinned
  owner identity, so a request that reaches it without passing Access cannot
  use the tools.

Accepted limitations: Cloudflare handles decrypted traffic and keeps
provider-side logs; OpenAI receives the tool results; immediate revocation of
issued tokens after a policy change is not guaranteed, so the route relies on a
verified emergency stop instead; and compatibility is confirmed only for the
deployed client configuration. Some behaviors have not been observed live: a
request from a second identity, which tests reject at the origin; signing-key
rotation; and the refresh-grant exchange, although access continues beyond the
token lifetime. The [trust boundary](#trust-boundary-and-data-handling) records
the data handling review.

WorkOS AuthKit is a fallback only if a demonstrated compatibility or identity
limitation of Managed OAuth remains after configuration and debugging;
account permissions or a blocked request do not prove one. Switching to it
changes the trust boundary, so it needs a new design review.

## Trust boundary and data handling

TLS ends at the Cloudflare edge, and the tunnel encrypts traffic between
Cloudflare and cloudflared. The last hop is plain HTTP on loopback, with
cloudflared and the server running in the same VM. Cloudflare therefore
handles decrypted requests and responses, including queries and note
excerpts, and keeps its own operational logs. ChatGPT sends tool results to
OpenAI's models, as local hosts send them to their providers. The
application's [log policy](http-contract.md#logging) covers only this
application's logs, not Cloudflare's or OpenAI's.

Text that a client writes is untrusted, like note text. A proposal can hold
anything the conversation produced, including instructions that a note or a
web page injected. It reaches the knowledge vault only through the vault
owner's review of the diff, and the read tools describe inbox notes as
unreviewed. Proposal text crosses Cloudflare and OpenAI like excerpts do.

Compatibility between ChatGPT, Managed OAuth, and the SDK's HTTP transport
is confirmed for the example deployment's configuration only. Before real
notes are served, review the dependencies above and the client account's
training, retention, and admin access. For the example deployment, the vault
owner authorized the [exposed scope](#exposed-scope) after this review:

- **Client account.** The client is a ChatGPT Business workspace that the
  vault owner's lab administers. OpenAI does not train on Business content by
  default, and the vault owner confirmed that the training setting is off.
  The workspace sets no retention policy of its own, and the lab's rules
  allow connecting personal data. Workspace admins control developer mode, so
  they can disable the connector.
- **Admin access.** OpenAI's sources conflict on whether Business admins can
  read members' conversations: its Business help pages say that admins cannot
  see members' private chats and that data export is not available, but the
  Business section of its [enterprise privacy
  page](https://openai.com/enterprise-privacy/) says that admins can view,
  access, export, and delete conversations. Treat chats that contain note
  excerpts as possibly visible to the workspace admins.
- **Retention.** Chats are kept until deleted. After a member leaves the
  workspace, the workspace keeps their chats; they are not transferred.

These account statements come from an external model's reading of OpenAI's
pages and from search snippets, because the pages refused automated access;
they were not checked page by page. Recheck them after a change of client
account or plan; another deployment needs its own review.

## Edge configuration

- A dedicated self-hosted Access application, with Managed OAuth enabled,
  covers the whole MCP hostname. No other service shares the hostname.
- An allow policy admits only the vault owner's identity, with no bypass for
  tool routes.
- The tunnel routes only the MCP hostname to the loopback origin and returns
  404 for unmatched requests.
- No Cloudflare caching rule overrides the origin's `Cache-Control: no-store`.
- If requests fail, inspect Cloudflare's challenge and security events, and
  change only a setting shown to be incompatible, as narrowly as the service
  plan allows. Do not disable protections in bulk or assume that every feature
  can be excluded per host; if the plan cannot scope a change, reassess.

## Discovery and registration

Cloudflare Access serves OAuth discovery and registration at the edge; the
origin serves only the MCP endpoint. If a future integration requires
origin-served discovery, it needs a separate specification and must never
expose tools without a valid assertion. ChatGPT registers with dynamic client
registration, which it selects automatically because Cloudflare does not
advertise client ID metadata documents (CIMD). Revisit CIMD only if the
provider advertises support and there is a concrete operational reason to
change.

## Runtime isolation

The single-owner, best-effort assumptions in [scope and
context](#scope-and-context) keep the runtime simple. They do not relax the
authentication, path, key-cache, resource, or logging protections, which are
part of remote exposure, not later cleanup. Investigate capacity or
performance only if ordinary use reveals errors or unacceptable delays.

Each write request is bounded, but the inbox has no bound on its total size
or number of proposals. Only the vault owner can call the tools, and the
vault owner deletes merged proposals. A full inbox can fill the VM's disk,
but it cannot change a canonical note. Do not rely on the client to confirm
writes: in the example deployment, ChatGPT wrote without asking when the
vault owner had asked it to write. Only ChatGPT has been used to write.

### VM services and network

The server and cloudflared run as systemd services in the same VM,
communicating over loopback; the [guide for web
clients](use-with-web-clients.md) installs and operates them. cloudflared runs
a dashboard-managed tunnel, installed with `cloudflared service install`, so
the tunnel's routes are configured in the Cloudflare dashboard. No containers
or hypervisor-specific deployment configuration are needed.

- **Synchronization.** The server never runs Git. A cron job of the vault
  owner's account fast-forwards a dedicated checkout, so synchronization
  stays under the vault owner's control, and a diverged history stops the
  pull instead of merging. A bare Git remote alone cannot serve as the
  readable collection. Add snapshot machinery only for a demonstrated need.
- **Merging proposals.** The vault owner merges proposals in this checkout
  and pushes them with the account's key, so the key needs push access.
  The vault owner stops the service for the review, so no write can change
  a proposal while it is merged; the remote route is unavailable for those
  minutes. An edit merges with `git merge-file` against its base copy, so
  changes that reached the note after the proposal was made are kept or
  shown as conflicts.
- **Forwarded Host.** The dashboard-managed tunnel's route sends requests to
  `http://127.0.0.1:<port>` with **HTTP Host Header** set to `public_host`,
  which the gate accepts (`test_allowed_hosts` in `tests/test_http.py`).
  Cloudflare's [origin
  parameters](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/cloudflared-parameters/origin-parameters/)
  do not state which Host cloudflared sends when the setting is empty, so
  the route sets it explicitly. `127.0.0.1` instead of `localhost` avoids
  an IPv6 `::1` connection, on which the origin does not listen.
- **Outbound access.** cloudflared connects to Cloudflare, and the origin
  fetches signing keys from the [trusted key
  URL](http-contract.md#assertion-validation). Neither the unit nor this
  configuration restricts the network, so both paths stay open; a firewall
  added later must allow them.

### Exposed scope

The root is explicit configuration. It can be the whole checkout or one
subtree, with read-only access for the server except in the inbox when write
proposals are enabled; tool paths and citations are relative to that root.
A selection spread across several directories needs its own design. Only
`vault` mode serves real notes, and only for a scope that the vault owner has
authorized; a change of scope needs a new authorization. In the example
deployment, the vault owner authorized the whole dedicated checkout,
`/srv/knowledge-vault`, on 2026-10-05.

### Edge cache

By Cloudflare's [default cache
behavior](https://developers.cloudflare.com/cache/concepts/default-cache-behavior/),
checked on 2026-10-05, MCP `POST` requests to `/mcp` and Uvicorn's own 400 and
500 responses, which lack `Cache-Control: no-store`, are not cached; the edge's
handling of those Uvicorn responses has not been observed. A Cache Rule or Page
Rule such as "cache everything" could change that, so none may cover the MCP
hostname.

## Shutdown and revocation

Stopping cloudflared or the origin is the local shutdown: stopping either one
removes access, and access returns after it restarts. The [emergency
stop](use-with-web-clients.md#emergency-stop) stops and disables both. It
depends on management access to the VM: in the example deployment, through
Tailscale SSH or the Unraid VM console. The vault owner accepted that
dependency, so a Cloudflare-side route shutdown was not tested; Cloudflare does
not document how deleting a published route affects active connections. If a
stop without VM access becomes necessary, test deleting the route, with Access
protection in place, before relying on it.

Never disable or delete Access protection as a kill switch, because removing
the gate does not stop routing. A policy change alone is not a verified stop.
A dedicated, tested deny-all policy may add protection, but do not assume it
acts at once. Managed OAuth reevaluates policy when a token is refreshed, so a
policy change may not revoke issued tokens immediately. Measuring that delay
is optional; without a measurement, assume that issued tokens stay valid until
they expire.
