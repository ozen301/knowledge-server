# Implementation tasks

Run these sequentially. Tasks 1–7 are complete; their entries below keep
what exists, validation evidence, and what later tasks need. Task 8 is next.
Tasks 8–9 cover hardening and real-vault deployment, as the [web access
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
feature.

## Task 7 — HTTP entry point and synthetic ChatGPT trial (complete)

Completed on 2026-10-05. The local implementation, offline validation,
provisioning, and synthetic ChatGPT trial succeeded. The vault owner accepted
the evidence limitations below for closure. Real-vault HTTP support is not
implemented.

The `knowledge-server-http` launcher and its configuration, synthetic guard,
request gate, assertion verifier, key cache, and restricted logging are in
`src/knowledge_server/adapter/http*.py`, `synthetic.py`, and
`synthetic-vault.json`. The [architecture
overview](architecture.md#protected-http-entry-point) describes them, the
[HTTP contract](web-access.md#local-http-implementation-contract) defines
configuration and bounds, and the [usage
guide](usage.md#prepare-the-synthetic-http-trial) gives the launch procedure.
The owner identity was established privately and pinned in private runtime
configuration; the server has no bootstrap or claim-inspection mode.

Local evidence, 2026-10-04: `scripts/check` passed formatting, lint, type,
and whitespace checks and all 479 tests, 98 of them for HTTP. The offline
checks in `tests/test_http.py` use invented keys and injected key fetching.
They cover assertion and owner rejection, Host and Origin checks, bounded
key retrieval and rotation, synthetic-only startup, HTTP/stdio schema and
result parity, `no-store` responses, and exclusion of sensitive log content.
Real loopback subprocess checks cover unauthenticated denial, signal shutdown,
bind failures, and failed lifespan startup. Offline initialization negotiated
`2025-11-25`; this is not evidence of ChatGPT's negotiated version.

### Live trial evidence, 2026-10-05

The vault owner's trial report and follow-up confirmations record these
results; private deployment notes, identifiers, credentials, and logs stay
outside this repository. The client was a personal ChatGPT Plus account. The
origin ran on the Ubuntu VM at `127.0.0.1:8000`, serving a copy of
`tests/fixtures/vault/` in synthetic mode, with cloudflared `2026.9.3`.
Managed OAuth issued 15-minute access tokens (the default) with a 1-week
grant session duration. The Cloudflare identity provider had account-member
restriction enabled, the Access policy allowed only the vault owner, and the
origin required a valid signed assertion for its pinned owner subject. The
[OAuth settings](web-access.md#oauth-settings-validated-in-the-synthetic-trial)
record the illustrative endpoint and discovery URLs, the advertised
capabilities, and the redirect allowlist setting.

| Check | Observed result |
|---|---|
| Unauthenticated request to `http://127.0.0.1:8000/mcp` | 401 with `Cache-Control: no-store`; the listener stayed on loopback |
| Unauthenticated request to the public MCP endpoint | 401 with a `WWW-Authenticate` challenge naming the protected-resource metadata |
| Discovery and connection | Discovery worked; DCR registration and vault-owner authorization succeeded |
| Cited answer | Tool activity was visible; ChatGPT answered "AMD Ryzen 5 2600X", citing `infrastructure/nas-configuration.md`, line 7 |
| Reconnect | Tool access continued after disconnect and reconnect |
| Practical renewal | 20 minutes after the last successful call, with no use or reconnect, a fresh read in a new chat answered "ASRock B450M Pro4-F", citing line 6 of the same note |
| Stop and restart the origin while cloudflared runs | Access stopped, then recovered after the restart |
| Stop and restart cloudflared while the origin runs | Access stopped, then recovered after the restart |

Successful tool use through the implemented gate indicates compatibility with
the live assertion, key set, and stateless JSON transport. Because
`allowed_origins` was empty, it also indicates that requests reaching the
origin carried no `Origin` header; it does not show which headers ChatGPT
sent to Cloudflare. These are inferences from successful requests, not
captured headers or inspected claims.

### Accepted evidence limitations

The vault owner accepted Task 7 closure with these details unobserved. They
are not passed checks:

- The live negotiated MCP protocol version and a complete tool inventory
  with schemas and annotations were not recorded. Tests cover offline
  HTTP/stdio parity.
- The activity summary could not be expanded, so the individual tool calls,
  their order, and the use of all four tools were not visible.
- No refresh-grant exchange was captured. Continued access beyond the token
  lifetime supports renewal but does not show the exchange.
- The Host value that cloudflared forwarded was not recorded. The gate
  accepts both the public hostname and loopback names, so success does not
  show which one arrived.
- Live signing-key rotation and Cloudflare caching of Uvicorn-generated 400
  and 500 responses were not observed.
- The conditional second-identity denial test was not recorded. Tests cover
  offline rejection of non-owner assertions.

### Handoff

Task 8 addresses the Host, key-rotation, and cache items offline unless the
vault owner authorizes a live synthetic route, and Task 9 step 1 repeats the
live probes on the target runtime. The process stop/restart checks do not
establish Cloudflare route shutdown or issued-token revocation; Task 9
measures both before real-vault use. Running the full 19-question [retrieval
evaluation](retrieval-evaluation.md) through ChatGPT remains an optional
follow-up.

## Task 8 — Harden the remote runtime and package it for Unraid

Depends on: Task 7 (complete). Status: not started.

Harden the Task 7 entry point for permanent use, as the [web access
plan](web-access.md#runtime-isolation) describes. Choose exact values during
implementation and record the reasons.

- Add request, rate, and concurrency limits for remote requests.
- Extend key-fetch and restart resilience beyond the Task 7 baseline, and add
  startup and health diagnostics. If live signing-key rotation cannot be
  exercised, record it as unobserved.
- Specify the Host header that the packaged cloudflared sends to the origin,
  check that the origin configuration accepts it, and test this offline. The
  gate returns 421 for any Host other than the configured public hostname or
  a loopback name, and Task 7 did not record the forwarded value.
- Review the edge cache configuration for Uvicorn's own 400 and 500
  responses, which the application's `no-store` guarantee does not cover.
- Keep configuration and secrets outside Git.
- Package the server and cloudflared for Unraid: an isolated container network
  with no published origin port on the host, outbound access for cloudflared,
  a non-root process, and a read-only vault mount.
- Choose a dedicated local vault checkout or a read-only materialized
  snapshot, and document an external synchronization procedure.

Validate with invented notes and offline tests. This task needs no additional
account and no live public route. Unless the vault owner authorizes a live
synthetic route during this task, Task 9 step 1 makes the live observations.

Acceptance:

- The Task 7 offline tests and the stdio tests still pass; the same core
  behavior works through both transports.
- The runtime cannot write to the mounted vault, and the origin port is not
  published on the host. Restart and reconnection work without corrupting data
  or requiring a new index.
- Limits and safe logging apply to remote requests.
- The packaged configuration states the expected forwarded Host, offline
  tests show that the gate accepts it, and the edge cache review is recorded.
- Exact launch and deployment configuration, synthetic verification evidence,
  and rollback instructions are ready before the deployment approval for
  Task 9.

## Task 9 — Deploy and enable real-vault use

Depends on: Task 8 and the vault owner's authorization to deploy the
synthetic service where that authorization is still needed. Activating the
real vault scope needs the separate authorization in step 5.

Deploy the prepared service on Unraid with the synthetic vault, then work in
this order. Before step 6, separately specify, test, implement, and review the
real-vault launch path and its startup conditions. Preserve the synthetic
guard for synthetic mode; the current launcher cannot activate real notes.

1. Recheck Managed OAuth's status and the provider documentation. Then, with
   the synthetic vault, repeat these live probes and record the results.
   Capturing the refresh exchange and repeating the full retrieval
   evaluation are not required.

   - From inside the runtime network, because the host publishes no origin
     port, an unauthenticated request to the origin returns 401 with
     `Cache-Control: no-store`.
   - An unauthenticated request to the public endpoint returns 401 with the
     OAuth challenge.
   - Discovery and the vault owner's OAuth connection succeed.
   - A read returns a correctly cited answer.
   - Tool access continues after a reconnect. After a period longer than the
     access-token lifetime, with no use or reconnect, a fresh read in a new
     chat succeeds.
   - Stopping the origin or cloudflared, each while the other keeps running,
     removes access, and restarting it restores access.
   - The origin accepts the forwarded Host. Record whether the edge caches
     Uvicorn-generated 400 or 500 responses.
   - If another identity is available, it is refused. Otherwise, the live
     denial stays unverified, as the [implementation
     plan](implementation-plan.md#progressive-milestones) notes.

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
