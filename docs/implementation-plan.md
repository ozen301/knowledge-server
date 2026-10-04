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

Phase 1 uses Python 3.14 or later, uv, ripgrep, and the official MCP Python
SDK v2 (`mcp>=2.2,<3`). It exposes four read-only tools over stdio:
`knowledge_search`, `knowledge_read`, `knowledge_list`, and `knowledge_info`.
The server reads the local vault checkout, configured through
`KNOWLEDGE_ROOT`; the NAS-hosted Git remote is used for synchronization and is
not searchable.

Development starts on an Ubuntu VM, with eventual production hosting on
Unraid. After local validation, the next priority is connecting ChatGPT
through the [web access route](#web-access-route). Better retrieval,
additional document formats and collections, and controlled writing remain
later possibilities.

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

Phase 1 assumes the vault owner controls the local vault checkout and that
concurrent edits are trusted. Path checks and symlink rejection protect the
tool boundary, but they do not isolate the service from a hostile local process
running as the same OS user. Stronger isolation requires a restricted process
or container and appropriately limited mounts.

## Progressive milestones

| Milestone | Build | Exit condition |
|---|---|---|
| 1. Local read-only MVP (complete) | Four stdio tools, shared path policy, synthetic tests | A real host can search, read, and cite an invented note; denied paths and output limits work |
| 2. Retrieval evaluation (complete) | Predefined questions over invented English and Japanese notes | Results and failure causes are recorded; initial limits and Unicode matching are reviewed |
| 3. Web access | Authenticated Streamable HTTP entry point behind Cloudflare Access and Tunnel; synthetic ChatGPT trial, hardening, then authorized real-vault use | ChatGPT, signed in as the vault owner, can search, read, and cite a note; other identities are refused |
| 4. Better lexical retrieval, if needed | SQLite metadata and FTS5 with a rebuildable index | Measured retrieval or latency improves; stale and missing sources are handled |
| 5. Additional formats | Add one format at a time, likely text-based PDF first | Hits remain traceable to the original file and page or section |
| 6. Semantic retrieval, if needed | Evaluate multilingual embeddings and hybrid ranking | The saved evaluation improves while exact search and CPU-only operation remain useful |
| 7. Additional collections | Explicitly configured roots such as notes, papers, and projects | Source identity and filtering are consistent across tools and caches |
| 8. Controlled writing, optional | Separate proposal or inbox workflow with vault-owner review | Proposals cannot mutate canonical notes through the read-only service |

For Milestone 3, if no second identity is available for a live test, the
evidence that other identities are refused is the origin's offline rejection
of non-owner assertions and the owner-only Access policy; the live denial
remains unverified.

Web access does not depend on vector search or NAS-wide indexing.
Authentication, source-access policy, TLS, resource limits, and restricted
runtime mounts are part of remote exposure, not later cleanup.

For production, use a dedicated local vault checkout or a read-only
materialized snapshot. Keep synchronization outside the MCP process and handle
conflicts explicitly. A bare Git remote alone cannot serve as the readable
collection.

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
immediate revocation of issued tokens after a policy change is not guaranteed;
and the successful trial covers only invented notes and the tested client
configuration. Provider status, compatibility, and data handling are rechecked
before real-vault use, as the web access plan describes.

WorkOS AuthKit is a contingency only if a demonstrated compatibility or
identity limitation of Managed OAuth survives debugging; selecting it would
need a new design review.

## Deferred retrieval decisions

Preserve `knowledge_read` for line-oriented Markdown. If document conversion
introduces page or section citations, add an appropriate document identifier
and reader instead of forcing those formats into today's line model. Likewise,
add explicit ranked or hybrid search modes rather than changing literal mode.

NFC-equivalent matching is an important follow-up because visually identical
Unicode text can use different character sequences. The [retrieval
evaluation](retrieval-evaluation.md#deferred-nfc-equivalent-matching) showed
false no-answer results when the decomposed word was the only way to a note.
Any implementation must search normalized text while returning snippets,
paths, and line numbers from the original source. Width equivalence is a
separate decision.

SQLite FTS5, document converters, and vector stores are candidates for their
respective milestones, not Phase 1 dependencies. Choose them only after the
repeatable evaluation demonstrates a need.

## Implementation process

Implement and review one task at a time, in the order of the [implementation
tasks](implementation-tasks.md), with the workflow in
[AGENTS.md](../AGENTS.md). Do not add future backend abstractions solely to
mirror this roadmap. Revise the plan when evidence changes a decision, and
keep exact behavior in the contract and tests.
