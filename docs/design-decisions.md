# Design decisions

This document explains why knowledge-server works as it does: its goal, the
decisions to preserve, the remote route for web clients, and its trust
boundary. The [architecture overview](architecture.md) describes the
components, the [tool contract](tool-contract.md) and the [HTTP
contract](http-contract.md) specify exact behavior, and the
[roadmap](roadmap.md) lists optional later work.

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

The remote route needs one Linux host with systemd, here called the VM, that
runs the server and cloudflared as services.

The server is one Python package that runs as one process: an MCP adapter
over an MCP-free knowledge core. Use ordinary typed functions and data models;
avoid a plugin framework, dependency-injection container, or generic database
abstraction before a second backend exists.

## Decisions to preserve

1. **Read the local vault checkout.** Uncommitted and untracked eligible notes
   are visible. Git history is not searched, and synchronization remains a task
   for the vault owner, outside request handling.
2. **Use one visibility policy for every tool.** Hidden paths, symlinks, and
   unsupported types cannot become visible through another tool. Every
   symlink is rejected, even one to another note, because a symlink can point
   outside the vault and can change after a check. Git ignore rules are not
   an authorization system.
3. **Start with literal search.** `ECC Ryzen` means that literal phrase, not
   semantic similarity or an implicit AND query. Future search modes must not
   silently change this behavior. One call may give several queries, which
   are alternatives (OR): agents without semantic search try several
   wordings, and one call for all of them saves round trips and can reduce
   context use.
   Hits do not say which query matched, because ripgrep does not report it
   and adding it would need more passes or a matcher of our own.
4. **Make responses bounded and citable.** Return logical paths, line numbers,
   explicit truncation, and useful errors. Never expose server paths through
   raw exceptions. Read results number every line, because hosts cited wrong
   lines when a read returned only the first and last line numbers.
5. **Keep stored text inert.** Notes are retrieved data, never executable
   instructions. The service does not execute embedded code or automatically
   fetch Markdown URLs. Clients get one exception: the write-tool
   descriptions point them to the note conventions in `AGENTS.md` at the
   root, if it exists, because web clients do not otherwise see the
   conventions that agents working in the vault read. The usual place for
   such guidance is the server's MCP instructions, but Claude.ai does not
   pass them to the model, while both ChatGPT and Claude.ai pass tool
   descriptions. The descriptions state only where the conventions are and
   do not say when a client may call a tool, because ChatGPT flags such
   directions in tool descriptions as suspicious instructions. `AGENTS.md`
   itself therefore has to say which of its steps, such as shell commands,
   apply to clients of the server. The exception is acceptable because only
   the vault owner merges into `main`: a proposal for `AGENTS.md` stays on
   its unreviewed branch, and the read tools return it only when the agent
   names that branch.
6. **Keep operational state separate.** Operational state, such as future
   indexes and caches and the server's Git repository for proposals, lives
   outside both the vault and the source repository and can be rebuilt.
   Proposals are not operational state but pending note content, which
   cannot be rebuilt. They live on branches of the vault remote, so
   that the vault owner can review them in any clone.
7. **Use ripgrep for initial literal search.** Its integration must use the
   shared file policy, bounded subprocess output, deadlines, and cancellation.
   ripgrep is the current implementation choice, not a permanent requirement;
   [existing implementation choices](#existing-implementation-choices) states
   when to reconsider it.
8. **Writes are proposals that the vault owner reviews.** The service
   never changes a canonical note. Over HTTP, an agent can save new notes
   and edits as a [proposal](tool-contract.md#write-proposals), a branch of
   the vault remote; it takes effect only when the vault owner merges it
   into `main`. A new proposal starts from the commit of the local vault
   checkout, so that the vault owner can merge it like any other branch.
   Local hosts can edit the vault directly, so stdio stays read-only. The
   service unit, not only the path policy, keeps the local vault checkout
   read-only.

The server needs write access to the vault remote: an SSH key of the
service account, or, for a vault remote on a network share mounted on the
VM, write access to the repository's directory. Neither a plain SSH Git
remote nor file permissions can limit that access to some branches, so a
compromised server could change `main`. Unreviewed text also stays in the
history of the vault remote, its backups, and every clone that fetched a
proposal branch, even after the branch is deleted. The vault owner accepts
both risks, so that proposals can be reviewed in any clone with Git alone.

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
  in-process matcher only when performance work or a matching requirement
  that ripgrep cannot meet justifies changing the match stage. A replacement
  must keep the agreed case behavior, citations, budgets, cancellation, and
  skipped counts. It is not known whether Python
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

Web clients, ChatGPT and Claude.ai, connect to a public HTTPS endpoint on a
Cloudflare-managed domain. Cloudflare Access, with Managed OAuth and an
owner-only policy, signs the vault owner in; Cloudflare Tunnel then forwards
each request, with a signed assertion, to an HTTP entry point in the adapter
layer. The core stays MCP-free, and stdio and the tool contracts stay
unchanged. The [HTTP contract](http-contract.md) specifies the entry point.

Reasons for this route:

- OpenAI's Secure MCP Tunnel required an account association that could not
  be established. This route does not depend on it.
- Cloudflare acts as the OAuth authorization server, so the project
  implements no OAuth server. It also serves OAuth discovery and dynamic
  client registration at the edge, so the origin serves only the MCP
  endpoint.
- cloudflared connects outbound, so no inbound router port forwarding is
  needed, and administration stays on a private network.
- The origin also validates Cloudflare's signed assertion and the pinned
  owner identity, so a request that reaches it without passing Access cannot
  use the tools.

Accepted limitations: Cloudflare handles decrypted traffic and keeps
provider-side logs, the client's provider receives the tool results, and a
policy change does not revoke issued tokens at once, so the route relies on
the [emergency stop](#shutdown-and-revocation) instead. Replacing Managed
OAuth, for example with WorkOS AuthKit, changes the trust boundary and needs
a new design review.

## Trust boundary and data handling

TLS ends at the Cloudflare edge, and the tunnel encrypts traffic between
Cloudflare and cloudflared. The last hop is plain HTTP on loopback, with
cloudflared and the server running in the same VM. Cloudflare therefore
handles decrypted requests and responses, including queries and note
excerpts, and keeps its own operational logs. The client sends tool results
to its provider, OpenAI or Anthropic, as local hosts send them to theirs. The
application's [log policy](http-contract.md#logging) covers only this
application's logs, not those of Cloudflare or the providers.

Text that a client writes is untrusted, like note text. A proposal can hold
anything the conversation produced, including instructions that a note or a
web page injected. It reaches the knowledge vault only through the vault
owner's review of the diff, and the read tools return proposal text only
when the agent names a proposal branch. Proposal text crosses Cloudflare and
the provider like excerpts do.

Because these parties see note text, real notes are served only after the
vault owner has reviewed them and the client account's training, retention,
and admin access, and has authorized the [exposed scope](#exposed-scope).

## Edge configuration

The edge admits only the vault owner and nothing else reaches the origin:

- One dedicated Access application covers the whole MCP hostname, no other
  service shares the hostname, and the tunnel routes only this hostname to
  the origin. Another route or policy on the hostname could otherwise expose
  the tools.
- The allow policy admits only the vault owner's identity, with no bypass for
  tool routes.
- No Cache Rule or Page Rule covers the MCP hostname, so the edge never
  serves a stored response, which could contain note text, to another
  request.
- If Cloudflare blocks requests, change only a setting shown to be
  incompatible, as narrowly as the plan allows, so that the other
  protections stay in place. Do not disable protections in bulk.

## Runtime isolation

The single-owner, best-effort assumptions in [scope and
context](#scope-and-context) keep the runtime simple. They do not relax the
authentication, path, key-cache, resource, or logging protections, which are
part of remote exposure, not later cleanup. Investigate capacity or
performance only if ordinary use reveals errors or unacceptable delays.

Each write request and proposal listing is bounded, but nothing bounds the
total size or number of proposals. Only the vault owner can call the tools,
and the vault owner deletes merged and rejected branches. Objects that
accumulate in the server's repository can fill the VM's disk, but they
cannot change a canonical note. Do not rely on the client to confirm writes:
ChatGPT has written without asking when the vault owner had asked it to
write.

### VM services and network

The server and cloudflared run as systemd services in the same VM and
communicate over loopback; the [guide for web
clients](use-with-web-clients.md) installs and operates them. No containers
or hypervisor-specific configuration are needed.

- **Synchronization.** A cron job of the vault owner's account fast-forwards
  a dedicated checkout, so synchronization stays under the vault owner's
  control, and a diverged history stops the pull instead of merging. The
  server only reads the checkout. A bare Git remote alone cannot serve as
  the readable collection. Add snapshot machinery only for a demonstrated
  need.
- **Merging proposals.** The server only reads the local vault checkout, so
  it creates proposal commits in a bare Git repository of its own and pushes
  them to the vault remote. It copies the checkout's history into that
  repository instead of borrowing the checkout's objects through a Git
  object alternate, because garbage collection in the checkout could delete
  borrowed objects that the server still needs. The copy costs about as much
  disk space as the checkout's history. The repository holds nothing that
  exists only there once a push succeeds, so it can be deleted while the
  server is stopped, and the next start rebuilds it. The vault owner merges
  or deletes proposal branches in any clone, and the cron pull brings the
  merged notes to the local vault checkout. The review needs no session on
  the VM and no server stop.

### Exposed scope

The root is explicit configuration. It can be the whole checkout or one
subtree, with read-only access for the server; tool paths and citations are
relative to that root. A selection spread across several directories needs
its own design. Only `vault` mode serves real notes, and only for a scope
that the vault owner has authorized; a change of scope needs a new
authorization.

## Shutdown and revocation

Stopping cloudflared or the origin is the local shutdown: stopping either one
removes access, and access returns after it restarts. The [emergency
stop](use-with-web-clients.md#emergency-stop) stops and disables both. It
depends on management access to the VM, which the vault owner accepted.
Cloudflare does not document how deleting a published route affects active
connections, so a stop from the Cloudflare side is not relied on; test it,
with Access protection in place, before relying on it.

Never disable or delete Access protection as a kill switch, because removing
the gate does not stop routing. A policy change alone is not a verified stop.
A dedicated, tested deny-all policy may add protection, but do not assume it
acts at once. Managed OAuth reevaluates policy when a token is refreshed, so
assume that issued tokens stay valid until they expire.
