# Implementation tasks

Run these sequentially. Tasks 1–6a are complete; their entries below keep
only what exists and what later tasks need. Tasks 7–9 implement the vault
owner's next priority, web access for ChatGPT, as the [web access
plan](web-access.md) specifies. Define tasks for retrieval upgrades from the
[retrieval evaluation](retrieval-evaluation.md) findings.

For each task, follow **Spec -> Tests -> Implementation -> Validation -> Drift
prevention** as defined in [AGENTS.md](../AGENTS.md). Establish the behavior
and acceptance checks first, express meaningful behavior in tests before
implementing it, and finish by checking that the specification, tests, code,
and usage instructions agree. Keep one coherent, reviewable diff per task.

## Task 1 — Scaffold and verify SDK compatibility (complete)

The package, `pyproject.toml`, `uv.lock`, and the pytest, Ruff, and Pyright
configuration exist. The lockfile resolves `mcp` 2.2.0. The smoke test in
`tests/test_scaffold.py` shows the supported SDK usage: `MCPServer` from
`mcp.server.mcpserver`, `Client` and `StdioServerParameters` from `mcp`, and
a stdio server that starts and shuts down cleanly.

## Task 2 — Configuration, data models, and shared path policy (complete)

`config.py` loads and validates `KNOWLEDGE_ROOT`. `core/models.py` defines the
request and result models and the domain errors, `core/limits.py` defines each
limit once, and `core/paths.py` applies the path and visibility policy for
every tool.

## Task 3 — Bounded read, list, and metadata (complete)

`core/reader.py` provides the `read_note`, `list_directory`, and `note_info`
operations and the shared bounded loader.

Known limitation: if a listed directory is replaced by a symlink between the
policy check and the scan, the listing can return an empty page instead of
`ACCESS_DENIED`. Each entry is still checked from the root, so no names from
outside the root are exposed.

## Task 4 — Literal search through ripgrep (complete)

`core/search.py` provides `search_notes`, an `async` function that implements
[`knowledge_search`](phase-1-contract.md#knowledge_search) by sending the
loaded note text to one ripgrep process on standard input. The contract lists
its known limitations. `tests/test_search.py` runs the real ripgrep for
matching behavior and uses small shell scripts in its place for process
failures, timeouts, cancellation, and output budgets. The CI workflow installs
ripgrep from the Ubuntu packages, so CI tests a different ripgrep version than
local development (15.1.0).

## Task 5 — Expose the four tools over stdio (complete)

`adapter/server.py` registers the four tools on an SDK `MCPServer`, and
`__main__.py` checks `KNOWLEDGE_ROOT` and finds `rg` on `PATH` before it
serves them over stdio. `pyproject.toml` defines the `knowledge-server`
command. The [architecture overview](architecture.md#mcp-adapter) explains why
the adapter builds SDK `Tool` objects directly, and why an SDK upgrade needs a
check of the adapter's imports. Logging goes to stderr at the WARNING level;
an unexpected exception is logged with its type and source location only.

`tests/test_adapter.py` covers schemas, annotations, results, argument and
domain errors, and injected exceptions in-process, and runs the entry point as
a real stdio subprocess for the session, clean shutdown, stdout content, the
absence of TCP sockets, cancellation of a search, and startup failures.

## Task 6 — Local integration, documentation, and release check (complete)

The [usage guide](usage.md) documents setup for Claude Code and Codex CLI and
what the server reads; the README has a quick start. `tests/fixtures/vault/`
holds invented sample notes in English and Japanese. Claude Code 2.1.286 and
Codex CLI 0.159.3 both answered questions about the sample notes by searching
and reading, and cited the source note, without changing a Git copy of the
vault.

## Task 6a — Record repeatable retrieval evaluation (complete)

The [retrieval evaluation](retrieval-evaluation.md) holds 19 questions about
the sample notes, how to run and grade them, the baseline results, and the
known weaknesses. `tests/test_retrieval_evaluation.py` repeats the reference
queries and checks the answer lines. The evaluation led to numbered read lines
(`numbered_content` in `knowledge_read`) and left the initial limits
unchanged. NFC-equivalent matching is the most important deferred search
feature. Task 7 can rerun the question set through the web client to check its
tool use and citations.

## Task 7 — HTTP entry point and synthetic ChatGPT trial

Depends on: Task 6a. The live trial also needs a Cloudflare-managed domain and
the owner's login identity, provisioned by the vault owner or with their
authorization.

Status: in progress. The route is decided; the HTTP implementation and the
live trial have not started. The [implementation
plan](implementation-plan.md#web-access-route) gives the reasons for the
route. No tunnel or remote connection has been verified. An earlier local
readiness check passed all 105 adapter and retrieval-evaluation tests; they
use invented notes and show nothing about remote access.

Build the minimal HTTP entry point that later tasks keep, with its
protections and offline tests from the start; then use it for a synthetic
trial with a personal ChatGPT Plus account. Do not leave assertion signature
checks, fail-closed owner authorization, bounded key fetching and refresh, or
safe logs for Task 8. Work in this order:

1. **Spec.** Choose and justify the JWT library. Check the installed SDK's
   Streamable HTTP API, including stateless mode, JSON responses, and host
   protection. Record the entry point's configuration in the web access plan.
2. **Tests.** Write the offline tests listed below before the code.
3. **Implementation.** Build the entry point and all its protections as the
   [web access plan](web-access.md#origin-http-entry-point) describes.
4. **Validation.** Run `scripts/check` and review the diff. Do not expose the
   service until both pass.
5. **Provisioning.** With the vault owner's authorization, set up the domain,
   the Access application, the tunnel, and cloudflared on the Ubuntu VM,
   routing to the loopback origin.
6. **Owner identity.** Establish and pin the owner identifier as the [web
   access plan](web-access.md#owner-identity) describes. No tools are enabled
   until then.
7. **Live trial.** Run the live probes below with `tests/fixtures/vault/`.
8. **Drift prevention.** Record sanitized results in this entry, and update
   the architecture overview, usage guide, and README for what exists.

Acceptance, offline (automated, synthetic keys, no network):

- The origin rejects a request without dispatching it to MCP when the
  assertion is missing or malformed, has a forged signature, uses an
  algorithm other than RS256, has no expiry claim, is expired or not yet
  valid beyond the allowed skew, has the wrong issuer or audience (string or
  array form), or has a missing identity, a non-owner identity, or a service
  credential. It accepts a valid owner assertion, including one whose
  audience array contains the AUD tag.
- An opaque `Authorization` token alone and unsigned identity headers do not
  authenticate a request.
- With an injected key provider: trusted keys come only from the configured
  source, and URLs or keys embedded in a token are not trusted. After a key
  rotation, an assertion with a new key ID passes once a permitted refresh
  finds the key. A key ID that stays unknown after refresh is rejected.
  Refreshes stay within their bounds, and a fetch timeout or key-source
  outage with no usable trusted key rejects the request.
- The entry point does not start without a configured owner identifier. The
  trial launch does not start without its explicit synthetic-only
  configuration.
- An invalid Host or an unapproved Origin is rejected; a request without
  Origin is accepted.
- Over HTTP, the four tools have semantically equal schemas and annotations
  and return the same results as over stdio. Responses carry
  `Cache-Control: no-store`. The stdio tests still pass.
- Sentinel tests show that tokens, claims, headers, queries, and note text do
  not reach the logs.
- `scripts/check` passes.

Acceptance, live synthetic trial with ChatGPT:

- An unauthenticated request receives the public 401 challenge, and the
  discovery metadata is reachable without login. Record the advertised
  resource identifier and registration methods.
- Registration uses a method that both sides support (CIMD, DCR, or a
  predefined client), with any redirect URI copied exactly from ChatGPT's
  server management page. Authorization uses PKCE `S256`, and discovery and
  token requests use the same advertised resource value.
- The owner signs in and the session initializes. Record the negotiated
  protocol version. Assertions from this flow carry the pinned owner
  identifier.
- ChatGPT discovers the four tools with semantically equal schemas and
  annotations.
- Asked which CPU the NAS uses, ChatGPT searches, reads, and answers "AMD
  Ryzen 5 2600X", citing `infrastructure/nas-configuration.md`, line 7.
- Token refresh and reconnection work.
- Stopping the origin, and separately stopping cloudflared, ends access;
  restarting recovers it.
- If a second identity is available, the edge denies it. If not, record the
  missing live test; documentation is not a pass.
- No live credential, claim, private configuration, or log is committed or
  included in a review handoff.

If requests are blocked, follow the [edge
configuration](web-access.md#edge-configuration) rules. If a compatibility or
identity limitation remains after debugging, record it; only then does the
[WorkOS AuthKit contingency](web-access.md#contingency) apply.

## Task 8 — Harden the remote runtime and package it for Unraid

Depends on: Task 7.

Harden the Task 7 entry point for permanent use, as the [web access
plan](web-access.md#runtime-isolation) describes. Choose exact values during
implementation and record the reasons.

- Add request, rate, and concurrency limits for remote requests.
- Extend key-fetch and restart resilience beyond the Task 7 baseline, and add
  startup and health diagnostics.
- Keep configuration and secrets outside Git.
- Package the server and cloudflared for Unraid: an isolated container network
  with no published origin port on the host, outbound access for cloudflared,
  a non-root process, and a read-only vault mount.
- Choose a dedicated local vault checkout or a read-only materialized
  snapshot, and document an external synchronization procedure.

Acceptance:

- The Task 7 offline tests and the stdio tests still pass; the same core
  behavior works through both transports.
- The runtime cannot write to the mounted vault, and the origin port is not
  published on the host. Restart and reconnection work without corrupting data
  or requiring a new index.
- Limits and safe logging apply to remote requests.
- Exact launch and deployment configuration, synthetic verification evidence,
  and rollback instructions are ready before the deployment approval for
  Task 9.

## Task 9 — Deploy and enable real-vault use

Depends on: Task 8 and the vault owner's authorization to deploy the
synthetic service where that authorization is still needed. Activating the
real vault scope needs the separate authorization in step 5.

Deploy the prepared service on Unraid with the synthetic vault, then work in
this order:

1. Recheck Managed OAuth's status and the provider documentation, and rerun
   the critical live probes from Task 7 with the synthetic vault.
2. Test shutdown from Cloudflare as the [web access
   plan](web-access.md#shutdown-and-revocation) describes: disable the
   tunnel's public-hostname route while Access stays in place, and record the
   configuration behavior, the effect on active connections, and the observed
   time to take effect. If this does not work reliably, choose and verify
   another supported routing shutdown.
3. Measure whether and when a policy change stops an already issued token
   from working.
4. Revisit data handling for the chosen client account, including the
   personal ChatGPT Plus account. If the account is a lab or workspace
   account, also check its permissions and governance.
5. Immediately before activating the real vault scope, obtain the vault
   owner's explicit authorization, informed by the results above and the
   dependencies disclosed in the [web access
   plan](web-access.md#trust-boundary-and-data-handling). Skip this request if
   the vault owner has already authorized this exact activation. No separate
   Beta approval is needed.
6. Activate and mount the real vault scope through explicit configuration,
   and repeat representative questions.

Keep credentials and private excerpts out of the repository.

Acceptance:

- ChatGPT discovers the tools, finds a known note, reads its relevant content,
  and provides a useful source citation.
- The route shutdown, policy-revocation, and data-handling results are
  recorded before the real vault scope is enabled.
- Restart and ordinary synchronization recover cleanly.
- Record the operational runbook: start/stop, route shutdown, update/rollback,
  sync, credential rotation, and connection troubleshooting.
- Declare completion for the tested client only. Connecting the other provider
  is a follow-up with its own connectivity/authentication checks.

## Follow-up — Review orchestration efficiency

This ongoing item does not block the tasks above. After the next two or three
substantial orchestrated tasks, or when the same friction recurs, review the
runs for avoidable cost: retries, duplicate inspection or validation, weak
handoffs, and blocked commands. Change the [orchestration
skill](../.agents/skills/orchestrated-implementation/SKILL.md) only when the
evidence supports a concrete improvement, following its rule for changes, and
record sanitized conclusions only. Delay an implementation task only for a
correctness, security, or privacy risk.
