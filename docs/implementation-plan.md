# Implementation plan

Status: agreed plan, 2026-09-17. Task 1 has established the package scaffold
and SDK compatibility check. The Phase 1 tools remain unimplemented.

## Outcome

Build a small local MCP server that lets an agent find an existing note, read
the relevant lines, and cite its vault-relative path. Make that workflow useful
before adding indexing, document conversion, or remote hosting.

The existing Markdown files remain authoritative. Any future database,
extracted text, or embedding index must be rebuildable from original sources.

This plan records the architecture, rationale, and roadmap. The
[Phase 1 contract](phase-1-contract.md) defines exact tool behavior, while the
[implementation tasks](implementation-tasks.md) define the implementation order
and acceptance checks.

## Scope and context

Phase 1 uses Python, uv, ripgrep, and the official MCP Python SDK v2. It exposes
four read-only tools over stdio: `knowledge_search`, `knowledge_read`,
`knowledge_list`, and `knowledge_info`. The server reads a local checkout of
the knowledge vault supplied through `KNOWLEDGE_ROOT`; the NAS-hosted Git
remote is used for synchronization and is not searchable.

Development starts on an Ubuntu VM, with eventual production hosting on
Unraid. After local validation, the next priority is connecting a ChatGPT or
Claude web client. Better retrieval, additional document formats and
collections, and controlled writing remain later possibilities.

## Architecture

```text
MCP host in VM
    | launches a subprocess; communicates over stdio
    v
MCP adapter: schemas, descriptions, error mapping
    |
    v
Knowledge core: access policy, read/list/info, literal search
    |
    v
Local vault checkout: KNOWLEDGE_ROOT
```

Keep this in one Python package and one process. The core must not import the
MCP SDK. Use ordinary typed functions and data models; avoid a plugin framework,
dependency-injection container, or generic database abstraction before a
second backend exists.

The initial package layout is:

```text
src/knowledge_server/
    __init__.py
    __main__.py          # startup checks and stdio launch
    config.py            # explicit configuration
    core/
        __init__.py
        models.py        # requests, results, and domain errors
        paths.py         # shared path and visibility policy
        reader.py        # bounded file loading, read/list/info
        search.py        # controlled ripgrep execution
    adapter/
        __init__.py
        server.py        # four thin tool wrappers
tests/
    fixtures/vault/      # invented notes only
    test_paths.py
    test_reader.py
    test_search.py
    test_mcp.py
```

Target Python 3.13 or later and initially constrain the official SDK to
`mcp>=2.2,<3`. Use pytest, Ruff, and Pyright. The implementation tasks verify
exact SDK imports, resolved dependency versions, and interpreter compatibility
when the project is scaffolded; these details should not be assumed from
tutorial code.

## Decisions to preserve

1. **Read the local vault checkout.** Uncommitted and untracked eligible notes are
   visible. Git history is not searched, and synchronization remains an
   owner-operated task outside request handling.
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

Phase 1 assumes the owner controls the local checkout and that concurrent edits
are trusted. Path checks and symlink rejection protect the tool boundary, but
they do not isolate the service from a hostile local process running as the
same OS user. Stronger isolation requires a restricted process or container
and appropriately limited mounts.

## Progressive milestones

| Milestone | Build | Exit condition |
|---|---|---|
| 1. Local read-only MVP | Four stdio tools, shared path policy, synthetic tests | A real host can search, read, and cite an invented note; denied paths and output limits work |
| 2. Retrieval evaluation | Predefined questions over invented English and Japanese notes | Results and failure causes are recorded; initial limits and Unicode matching are reviewed |
| 3. Web access | Select and validate a ChatGPT or Claude connection route, then deploy it securely | The chosen client can authenticate, search, read, and cite a note |
| 4. Better lexical retrieval, if needed | SQLite metadata and FTS5 with a rebuildable index | Measured retrieval or latency improves; stale and missing sources are handled |
| 5. Additional formats | Add one format at a time, likely text-based PDF first | Hits remain traceable to the original file and page or section |
| 6. Semantic retrieval, if needed | Evaluate multilingual embeddings and hybrid ranking | The saved evaluation improves while exact search and CPU-only operation remain useful |
| 7. Additional collections | Explicitly configured roots such as notes, papers, and projects | Source identity and filtering are consistent across tools and caches |
| 8. Controlled writing, optional | Separate proposal or inbox workflow with owner review | Proposals cannot mutate canonical notes through the read-only service |

Web access does not depend on vector search or NAS-wide indexing. At that
milestone, first check the selected account and client capabilities. Prefer a
supported private tunnel to the stdio service when available; otherwise use an
authenticated Streamable HTTP endpoint reachable by the selected provider.
Authentication, source-access policy, TLS, resource limits, and restricted
runtime mounts are part of remote exposure, not later cleanup.

For production, use a dedicated checked-out vault or read-only materialized
snapshot. Keep synchronization outside the MCP process and handle conflicts
explicitly. A bare Git remote alone cannot serve as the readable collection.
Tasks 7-9 defer route-specific decisions until current provider capabilities
and the user's account access can be verified.

## Deferred retrieval decisions

Preserve `knowledge_read` for line-oriented Markdown. If document conversion
introduces page or section citations, add an appropriate document identifier
and reader instead of forcing those formats into today's line model. Likewise,
add explicit ranked or hybrid search modes rather than changing literal mode.

NFC-equivalent matching is an important follow-up because visually identical
Unicode text can use different character sequences. Task 6a evaluates this
limitation before larger retrieval upgrades. Any implementation must search
normalized text while returning snippets, paths, and line numbers from the
original source. Width equivalence is a separate decision.

SQLite FTS5, document converters, and vector stores are candidates for their
respective milestones, not Phase 1 dependencies. Choose them only after the
repeatable evaluation demonstrates a need.

## Implementation process

Follow the [implementation tasks](implementation-tasks.md) in order and apply
the repository workflow in [AGENTS.md](../AGENTS.md). Implement and review one
task at a time; do not add future backend abstractions solely to mirror this
roadmap. Revise the plan when evidence changes a decision, and keep exact
behavior in the contract and tests.
