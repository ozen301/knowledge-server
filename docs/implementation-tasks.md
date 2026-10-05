# Implementation tasks

Run these sequentially. Tasks 1–8 are complete; their entries below keep
what exists, validation evidence, and what later tasks need. Task 9
(deployment and real-vault activation) is next, as the [web access
plan](web-access.md) specifies. Task 9 reaches the planned completion point.
Later work, starting with [NFC-equivalent
matching](#follow-up--nfc-equivalent-matching), is optional; define such tasks
from evaluation evidence, as the [implementation
plan](implementation-plan.md#later-possibilities) describes.

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

Task 9 step 1 repeats the live probes on the target runtime. Live signing-key
rotation is not exercised; record it as unobserved unless it is seen in use.
The process stop/restart checks show that a local stop works while its host
can be reached. They do not establish a Cloudflare-side route shutdown or
issued-token revocation; Task 9 verifies a usable emergency stop before
real-vault use. Running the full 19-question [retrieval
evaluation](retrieval-evaluation.md) through ChatGPT remains an optional
follow-up.

## Task 8 — Prepare the HTTP service for permanent use in the VM (complete)

Completed on 2026-10-05, at commit `c91c9ae` for the code and service
configuration. No real notes are exposed.

What exists: the `vault` launch mode, request bounds, and diagnostic events in
`src/knowledge_server/adapter/http*.py`, specified in the [HTTP
contract](web-access.md#local-http-implementation-contract); the server's
systemd unit and an example configuration in `deploy/`, described in [runtime
isolation](web-access.md#runtime-isolation); and the [service
runbook](usage.md#run-the-http-service-in-the-vm). The vault owner decided
that the tunnel stays dashboard-managed and is installed with `cloudflared
service install`, that a cron job of the vault owner's account synchronizes
the dedicated checkout, and that both launch modes remain.

Offline evidence: `scripts/check` passed formatting, lint, type, and
whitespace checks and all 494 tests, 113 of them for HTTP. They cover each
bound, the event categories without sensitive content, both launch modes, and
the writable-root refusal, and `test_allowed_hosts` covers the forwarded Host.

Service evidence, 2026-10-05: a scripted run of the runbook on the Ubuntu
26.04 VM, with invented notes only, passed all 14 checks. The server unit
was enabled for boot, ran as `knowledge-server`, listened only on
`127.0.0.1:8000`, and answered an unauthenticated `/mcp` request with 401 and
`Cache-Control: no-store` after start, restart, and stop and start. A local
Git repository of invented notes stood in for the vault remote: the crontab's
pull command fast-forwarded the checkout, and the service account could read
the pulled note. In vault mode under the unit, the server started, saw the
checkout as a read-only mount, could not write to it, and could not read the
administrator's home directory. The probes carried no assertion, so they
caused no key fetch.

The [edge cache review](web-access.md#edge-cache) is recorded, and the vault
owner confirmed that no Cache Rule or Page Rule covers the MCP hostname. The
outbound paths were reviewed in the configuration only.

For Task 9: the server is installed at `/opt/knowledge-server` with the real
Access values and runs in synthetic mode, serving the sample notes from the
installed checkout. cloudflared, the vault checkout, and the crontab line are
not installed. Live
key retrieval, the forwarded Host on the live route, and the edge's handling
of Uvicorn's own 400 and 500 responses remain unobserved.

## Task 9 — Deploy and enable real-vault use

Depends on: Task 8 (complete) and the vault owner's authorization to deploy
the synthetic service where that authorization is still needed. Activating a
real vault scope needs the separate authorization in step 5.

Install the Task 8 service configuration in the VM with the invented notes,
then work in this order. Record anything that cannot be observed as
unobserved, not as passed.

1. Recheck Managed OAuth's status and the provider documentation that the
   route depends on. Then, with the invented notes, make these live probes
   and record the results:

   - From the VM, an unauthenticated request to the loopback origin returns
     401 with `Cache-Control: no-store`.
   - An unauthenticated request to the public endpoint returns 401 with the
     OAuth challenge.
   - Discovery and the vault owner's OAuth connection succeed.
   - A read returns a correctly cited answer. The first authorized call after
     an origin start shows that the origin retrieved the signing keys, and the
     diagnostics show no key-retrieval failure.
   - Tool access continues after a reconnect. After a period longer than the
     access-token lifetime, with no use or reconnect, a fresh read in a new
     chat succeeds.
   - Stopping the origin or cloudflared, each while the other keeps running,
     removes access, and restarting it restores access.
   - The origin accepts the configured forwarded Host. If practical, record
     whether the edge caches Uvicorn-generated 400 or 500 responses.
   - If another identity is available, it is refused. Otherwise, the live
     denial stays unverified, as the [implementation
     plan](implementation-plan.md#progressive-milestones) notes.

   Capturing a refresh-grant exchange and repeating the full retrieval
   evaluation are not required.
2. Verify one usable emergency-stop procedure on the target runtime, and
   state the management access it depends on. A Cloudflare-side route
   shutdown is optional only if the vault owner accepts that dependency; the
   owner has not accepted it yet. Otherwise, also verify the route shutdown
   that the [web access plan](web-access.md#shutdown-and-revocation)
   describes.
3. Do not count a policy change as a stop. Measuring when a policy change
   stops an already issued token is optional; without that measurement,
   assume that issued tokens stay valid until they expire.
4. Revisit data handling for the chosen client account, including the
   personal ChatGPT Plus account. If the account is a lab or workspace
   account, also check its permissions and governance.
5. State the exact real scope to expose: the whole dedicated checkout or one
   subtree of it. Immediately before activation, obtain the vault owner's
   explicit authorization for that scope, informed by the results above and
   the dependencies disclosed in the [web access
   plan](web-access.md#trust-boundary-and-data-handling). Skip this request if
   the vault owner has already authorized this exact activation. Approval of
   plans, code, or the synthetic deployment does not authorize it. No
   separate Beta approval is needed.
6. Activate the real-vault mode for the authorized scope through explicit
   configuration, with read-only access for the server. The vault owner tries
   a few representative questions and checks the answers and citations.
   Investigate capacity or performance only if errors or unacceptable delays
   occur. Keep notes, questions, and answers private; record only a
   sanitized outcome. Check that restart and an ordinary synchronization
   recover cleanly.
7. Check the runbook against the target runtime and correct it.

Keep credentials and private excerpts out of the repository.

Acceptance:

- ChatGPT discovers the tools, finds a known note in the authorized scope,
  reads its relevant content, and provides a useful source citation.
- The emergency stop is verified and its access dependency stated, and the
  Cloudflare-side route shutdown is verified unless the vault owner accepted
  that dependency. These results and the data-handling review are recorded
  before the real scope is enabled.
- Restart and ordinary synchronization recover cleanly.
- The runbook matches the target runtime.
- Declare completion for the tested client only. Connecting the other provider
  is a follow-up with its own connectivity/authentication checks.

Task 9 reaches the planned completion point; later work is optional.

## Follow-up — NFC-equivalent matching

Normally follows Task 9; an observed retrieval failure that blocks use can
justify earlier work. Status: not started.

This is the first evidence-backed retrieval improvement: the [retrieval
evaluation](retrieval-evaluation.md#deferred-nfc-equivalent-matching)
recorded false no-answer results when decomposed text was the only way to a
note (E8, J6). Specify it before implementation, as the [implementation
plan](implementation-plan.md#deferred-retrieval-decisions) describes. Keep
snippets from the original text unless that specification decides otherwise;
snippets from normalized text are an option for that decision, not an agreed
change. If the work changes the match stage, it may consider an in-process
matcher under the conditions in [existing implementation
choices](implementation-plan.md#existing-implementation-choices). Rerun the
affected evaluation questions, and replace their baseline when the change is
adopted.

## Follow-up — Review orchestration efficiency

This ongoing item does not block the tasks above. After the next two or three
substantial orchestrated tasks, or when the same friction recurs, review the
runs for avoidable cost: retries, duplicate inspection or validation, weak
handoffs, and blocked commands. Change the [orchestration
skill](../.agents/skills/orchestrated-implementation/SKILL.md) only when the
evidence supports a concrete improvement, following its rule for changes, and
record sanitized conclusions only. Delay an implementation task only for a
correctness, security, or privacy risk.
