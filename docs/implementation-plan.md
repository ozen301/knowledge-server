# Implementation plan

Status: agreed plan, updated 2026-10-05. The
[implementation tasks](implementation-tasks.md) track progress.

## Outcome

Build a small local MCP server that lets an agent find an existing note, read
the relevant lines, and cite its vault-relative path. Make that workflow useful
before adding indexing, document conversion, or remote hosting.

The existing Markdown files remain authoritative. Any future database,
extracted text, or embedding index must be rebuildable from original sources.

This plan records the design decisions, their reasons, and the roadmap. The
[architecture overview](architecture.md) describes the components that exist,
the [Phase 1 contract](phase-1-contract.md) defines exact tool behavior, and
the [implementation tasks](implementation-tasks.md) define the implementation
order and acceptance checks.

## Scope and context

This is a personal project. The vault owner is its only user and maintains it
by hand, and availability is best effort: if the service stops, the owner
restarts it. These assumptions keep operations simple. They do not weaken the
note-access boundaries, which stay strict for every transport.

Phase 1 uses Python 3.14 or later, uv, ripgrep, and the official MCP Python
SDK v2 (`mcp>=2.2,<3`). It exposes four read-only tools over stdio:
`knowledge_search`, `knowledge_read`, `knowledge_list`, and `knowledge_info`.
The server reads the local vault checkout, configured through
`KNOWLEDGE_ROOT`; the NAS-hosted Git remote is used for synchronization and is
not searchable.

Development and permanent operation use an Ubuntu VM. The server and
cloudflared run as services inside that VM; Unraid is only the VM host, with
no application-specific deployment role. After local validation, the next
priority is connecting ChatGPT through the [web access
route](#web-access-route). A working personal remote retrieval service,
reached with Task 9, is the planned completion point. The
[later possibilities](#later-possibilities) are optional, and each needs
evidence of need before work starts.

The server is one Python package that runs as one process: an MCP adapter
over an MCP-free knowledge core. Use ordinary typed functions and data models;
avoid a plugin framework, dependency-injection container, or generic database
abstraction before a second backend exists. Check SDK details against the
installed version, not tutorial code.

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
   both the vault and the source repository.
7. **Use ripgrep for initial literal search.** Its integration must use the
   shared file policy, bounded subprocess output, deadlines, and cancellation.
   ripgrep is the current implementation choice, not a permanent requirement;
   [existing implementation choices](#existing-implementation-choices) states
   when to reconsider it.

Phase 1 assumes the vault owner controls the local vault checkout and that
concurrent edits are trusted. Path checks and symlink rejection protect the
tool boundary, but they do not isolate the service from a hostile local process
running as the same OS user. Stronger isolation requires a restricted process
with appropriately restricted filesystem permissions.

## Existing implementation choices

Keep the implemented tools, safeguards, and tests. Some parts are heavier than
a personal stdio tool needs, but they are tested, and the path checks, bounded
loader, request gate, and assertion check protect the vault once it is
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

The synthetic HTTP mode and its manifest guard remain after real-vault use is
enabled. They serve as a deployment and regression check with invented notes.

## Progressive milestones

| Milestone | Build | Exit condition |
|---|---|---|
| 1. Local read-only MVP (complete) | Four stdio tools, shared path policy, synthetic tests | A real host can search, read, and cite an invented note; denied paths and output limits work |
| 2. Retrieval evaluation (complete) | Predefined questions over invented English and Japanese notes | Results and failure causes are recorded; initial limits and Unicode matching are reviewed |
| 3. Web access | Authenticated Streamable HTTP entry point behind Cloudflare Access and Tunnel; synthetic ChatGPT trial (complete), permanent VM services with an explicit real-vault mode, then authorized real-vault use | ChatGPT, signed in as the vault owner, can search, read, and cite a note in the authorized scope; other identities are refused |

Milestone 3 is the planned completion point. If no second identity is
available for its live test, the evidence that other identities are refused
is the origin's offline rejection of non-owner assertions and the owner-only
Access policy; the live denial remains unverified.

Web access does not depend on vector search or NAS-wide indexing.
Authentication, source-access policy, TLS, resource limits, and restricted
filesystem access are part of remote exposure, not later cleanup.

For permanent use, give the server read-only access to a dedicated local
vault checkout through its service account and filesystem permissions. Keep
synchronization outside the MCP process and handle conflicts explicitly. A
bare Git remote alone cannot serve as the readable collection. The configured
root can select one subtree of the checkout; citations are then relative to
that subtree. A selection spread across several directories would need its
own design. Add snapshot machinery only for a demonstrated need.

During real-vault activation, the vault owner tries a few representative
questions. Investigate capacity or performance only if ordinary use reveals
errors or unacceptable delays; no separate capacity check blocks deployment.

## Later possibilities

These ideas are optional. Start one only when its trigger occurs, and define
it as a task backed by evaluation evidence. Do not build frameworks for them
in advance.

| Idea | Trigger and constraint |
|---|---|
| NFC-equivalent matching | The first evidence-backed retrieval improvement, normally after deployment; see [deferred retrieval decisions](#deferred-retrieval-decisions) |
| Multi-keyword queries, a rebuildable index such as SQLite FTS5, or ranking | Real questions fail because one literal phrase cannot combine separate words, or latency or search budgets block use; stale and missing sources must be handled |
| Additional formats, likely text-based PDF first | Needed sources exist outside Markdown; hits must remain traceable to the original file and page or section |
| Semantic retrieval with multilingual embeddings | The saved evaluation shows misses that lexical search cannot fix; exact search and CPU-only operation must remain useful |
| Additional collections | A second explicitly configured root is needed; source identity and filtering must stay consistent across tools and caches |
| Controlled writing | Writing becomes a need; proposals or an inbox with vault-owner review must not mutate canonical notes through the read-only service |

## Web access route

Decided on 2026-10-02. ChatGPT is the first web client, tested at first with
a personal ChatGPT Plus account. It connects to a public HTTPS endpoint on a
Cloudflare-managed domain. Cloudflare Access, with Managed OAuth and an
owner-only policy, signs the vault owner in; Cloudflare Tunnel then forwards
each request, with a signed assertion, to an HTTP entry point in the adapter
layer. The core stays MCP-free, and stdio and the four tool contracts stay
unchanged. The [web access plan](web-access.md) specifies the route, and
Tasks 7–9 implement it. The synthetic ChatGPT trial succeeded with Managed
OAuth and DCR; the [implementation tasks](implementation-tasks.md) record its
evidence limitations and define the hardening and real-vault work.

Reasons for this route:

- OpenAI's Secure MCP Tunnel required an account association that could not
  be established. This route does not depend on it.
- Cloudflare acts as the OAuth authorization server, so the project
  implements no OAuth server. ChatGPT's documented OAuth requirements for
  MCP servers ([OpenAI's authentication
  guide](https://developers.openai.com/plugins/build/auth)) are the
  compatibility target; the synthetic trial confirmed the integration.
- cloudflared connects outbound, so no inbound router port forwarding is
  needed. Tailscale remains the private administration network.
- The origin also validates Cloudflare's signed assertion and the pinned
  owner identity, so a request that reaches it without passing Access cannot
  use the tools.

Accepted limitations: Managed OAuth was documented as Beta when the design
was agreed; Cloudflare handles decrypted traffic and keeps provider-side logs;
immediate revocation of issued tokens after a policy change is not guaranteed,
so the plan relies on a verified emergency stop instead; and the successful
trial covers only invented notes and the tested client configuration.
Provider status, compatibility, and data handling are rechecked before
real-vault use, as the web access plan describes.

WorkOS AuthKit is a contingency only if a demonstrated compatibility or
identity limitation of Managed OAuth survives debugging; selecting it would
need a new design review.

## Deferred retrieval decisions

Preserve `knowledge_read` for line-oriented Markdown. If document conversion
introduces page or section citations, add an appropriate document identifier
and reader instead of forcing those formats into today's line model. Likewise,
add explicit ranked or hybrid search modes rather than changing literal mode.

NFC-equivalent matching is the first evidence-backed retrieval improvement,
because visually identical Unicode text can use different character sequences.
The [retrieval
evaluation](retrieval-evaluation.md#deferred-nfc-equivalent-matching) showed
false no-answer results when the decomposed word was the only way to a note.
It normally follows deployment; an observed retrieval failure that blocks use
can justify earlier work. Any implementation searches normalized text.
The current contract returns snippets, paths, and line numbers from the
original source, and that guarantee stays unless a focused NFC decision
changes it. Snippets from normalized text are an option for that decision:
paths and line numbers stay exact, but the quoted characters differ from the
file's bytes and can render differently. Width equivalence is a separate
decision.

SQLite FTS5, document converters, and vector stores are candidates for the
[later possibilities](#later-possibilities), not current dependencies. Choose
them only after the repeatable evaluation demonstrates a need.

## Implementation process

Implement and review one task at a time, in the order of the [implementation
tasks](implementation-tasks.md), with the workflow in
[AGENTS.md](../AGENTS.md). Do not add future backend abstractions solely to
mirror this roadmap. Revise the plan when evidence changes a decision, and
keep exact behavior in the contract and tests.
