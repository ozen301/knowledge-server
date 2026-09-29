# Implementation tasks

Run these sequentially. Tasks 1–5 are complete; their entries below keep only
what exists and what later tasks need. Task 6 covers the remaining Phase 1
work; Task 6a records the retrieval evaluation before web
integration. Tasks 7–9 outline the vault owner's next priority, web access;
finalize route-specific details in Task 7 before coding or deploying that
integration. Define tasks for retrieval upgrades from the evaluation
findings. Check the status of the [workflow improvement
plan](#workflow-improvement-plan) before starting each task.

For each task, follow **Spec -> Tests -> Implementation -> Validation -> Drift
prevention** as defined in [AGENTS.md](../AGENTS.md). Establish the behavior
and acceptance checks first, express meaningful behavior in tests before
implementing it, and finish by checking that the specification, tests, code,
and usage instructions agree.

## Task 1 — Scaffold and verify SDK compatibility (complete)

The package, `pyproject.toml`, `uv.lock`, and the pytest, Ruff, and Pyright
configuration exist. The lockfile resolves `mcp` 2.2.0. The smoke test in
`tests/test_scaffold.py` shows the supported SDK usage: it imports `MCPServer`
from `mcp.server.mcpserver` and `Client` and `StdioServerParameters` from
`mcp`, starts a temporary server with `server.run("stdio")`, connects a client,
and verifies a clean shutdown.

## Task 2 — Configuration, data models, and shared path policy (complete)

`config.py` loads and validates `KNOWLEDGE_ROOT`. `core/models.py` defines the
request and result models and the domain errors, `core/limits.py` defines each
limit once, and `core/paths.py` applies the path and visibility policy for
every tool. Folder allowlists are out of scope; the confirmed scope is all
non-hidden Markdown.

## Task 3 — Bounded read, list, and metadata (complete)

`core/reader.py` provides the `read_note`, `list_directory`, and `note_info`
operations and `load_note`, the shared bounded loader.
The loader opens each path component relative to its parent's descriptor
without following symlinks, so a symlink or FIFO swapped in after the policy
check is rejected.

Known limitation: if a listed directory is replaced by a symlink between the
policy check and the scan, the listing can return an empty page instead of
`ACCESS_DENIED`. Each entry is still checked from the root, so no names from
outside the root are exposed. Search discovery has the same race; see Task 4.

## Task 4 — Literal search through ripgrep (complete)

`core/search.py` provides `search_notes`, an `async` function that implements
`knowledge_search` as the
[contract](phase-1-contract.md#knowledge_search) defines it. On 2026-09-29
the vault owner chose to send the loaded note text to ripgrep on standard
input instead of file paths, and decided the [snippet
window](phase-1-contract.md#snippet-window). A worker thread discovers notes
with `PathPolicy.visible_child` and loads them with `reader.load_note_text`;
one ripgrep process then searches the text. `tests/test_search.py` runs the
real ripgrep for matching behavior and uses small shell scripts in its place
for process failures, timeouts, cancellation, and output budgets.

What Task 5 needs to know:

- `search_notes` takes the ripgrep executable path as `ripgrep=`; startup
  must find it. The adapter awaits `search_notes` directly. Cancelling the
  awaiting task kills and reaps ripgrep, and the loading thread stops at its
  next check.
- The CI workflow installs ripgrep from the Ubuntu packages, so CI tests a
  different ripgrep version than local development (15.1.0).

Observed latency on 2026-09-29, local SSD, ripgrep 15.1.0: 5-8 ms on a
five-note fixture, and about 330 ms on 2,000 synthetic notes (15.3 MiB). Of
the 330 ms, about 200 ms is the policy check of each entry during discovery
and about 100 ms is loading.

Known limitations, also described in the contract: equivalent Unicode forms
do not match until NFC support exists; many matches on very long lines can
exceed the output budget; a blocked filesystem call cannot be interrupted, so
the loading thread can outlive a search that already returned at its
deadline; and a directory replaced by a symlink during discovery drops its
notes without counting them.

## Task 5 — Expose the four tools over stdio (complete)

`adapter/server.py` registers the four tools on an SDK `MCPServer`, and
`__main__.py` checks `KNOWLEDGE_ROOT` and finds `rg` on `PATH` before it
serves them over stdio. `pyproject.toml` defines the `knowledge-server`
command. On 2026-09-29 the vault owner decided the [error
result](phase-1-contract.md#error-and-change-behavior) format, that
rejected arguments return `INVALID_ARGUMENT`, and that startup finds `rg` on
`PATH`. To meet the second decision, the adapter builds SDK `Tool` objects
directly instead of using the tool decorator; the
[architecture overview](architecture.md#mcp-adapter-adapterserverpy-and-__main__py)
explains why. GPT-6 Sol, consulted at the vault owner's request, agreed with
this design.

`tests/test_adapter.py` covers schemas, annotations, successful and empty
results, argument and domain errors, and injected exceptions in-process, and
runs the entry point as a real stdio subprocess for the session, clean
shutdown, stdout content, the absence of TCP sockets, cancellation of a
search, and startup failures.

What Task 6 needs to know:

- The command is `knowledge-server`, run through `uv run --project ...`, or
  `python -m knowledge_server`. It exits with status 1 and a stderr message
  when the root is unusable or `rg` is not found on `PATH`, so a host's
  `PATH` must contain `rg`. Startup does not run `rg`; if it fails when run,
  searches return `SEARCH_FAILED`.
- Logging goes to stderr at the WARNING level. An unexpected exception is
  logged at ERROR with its type and source location only.
- When upgrading the SDK, check the non-exported classes that
  `adapter/server.py` imports.

## Task 6 — Local integration, documentation, and release check

Depends on: Task 5.

Document the launch command and environment setup using portable example paths.
Replace these paths with local absolute paths when registering the host.
Planned form, to verify against the implemented entry point:

```sh
KNOWLEDGE_ROOT=/path/to/knowledge-vault \
  uv run --project /path/to/knowledge-server --locked knowledge-server
```

At the start of this task, select the first local MCP host and decide where to
record the manual check and timing evidence. No host choice is required for
Tasks 1–5. Document registration for the selected host using its current
supported configuration. Verify the command from an unrelated working
directory. Explain live-checkout semantics, manual Git synchronization, literal
search, exclusions, limits, and data disclosure: a connected model provider
can receive the note excerpts returned to its host.

Acceptance:

- `scripts/check` passes, including its checks for staged changes and new
  files (see [AGENTS.md](../AGENTS.md)).
- Run the complete suite on the minimum supported version with
  `uv run --python 3.14 --locked --dev pytest`.
- A synthetic end-to-end question produces a search hit, a read of the cited
  lines, and a correct source path in the host's answer.
- Measure search/read timings against the invented notes and record usability
  gaps without setting a performance guarantee from this small sample.
- Confirm the server makes no vault writes or Git changes. No network
  deployment is part of this task.
- If host access is unavailable, report exactly that manual check as pending;
  automated protocol tests alone do not complete the real-host acceptance gate.

The local MVP is complete only after these checks and the real-host workflow
pass. Do not claim future milestones are complete because the scaffolding could
support them.

## Task 6a — Record repeatable retrieval evaluation

Depends on: Task 6. This implements milestone 2.

Create `docs/retrieval-evaluation.md` with a small predefined question set and
expected source notes. Store the invented notes in `tests/fixtures/vault/`,
reusing suitable existing fixtures. Include English and Japanese examples,
questions with no answer in the notes, and the known Unicode matching
limitation. Record the natural-language question separately from the literal
search queries so a failed result can be traced to query choice, matching,
reading, or citation.

Acceptance:

- Each example names its invented source notes and expected answer or no-answer
  outcome. Run the questions through the local host and record the actual
  queries, retrieved paths, and whether the cited text supports the answer.
- Add repeatable search/read assertions to the automated tests for the literal
  queries and expected results. Tests depend only on the invented notes, not a
  model provider or personal vault.
- Record failures and their causes in the evaluation document. Use these
  examples to compare future retrieval changes.
- Review the initial limits against the observed results. Record whether
  adjustments are justified; apply any chosen adjustment to the code, contract,
  and tests together.
- Record NFC-equivalent matching as an important deferred feature and assess
  the demonstrated missed matches before proposing broader search upgrades. Its
  implementation requires a specification for normalized search input and
  original-text snippets; width matching remains a separate decision.

The vault owner may also evaluate personal notes privately. This is optional;
keep private questions, excerpts, paths, and results outside committed files
and automated fixtures.

## Task 7 — Choose and validate the first web-client route

Depends on: Task 6a and access to the selected client account.

Confirm which web client to connect first and inspect its available
developer/connector settings. These details can wait until this task. Recheck
the current official documentation for [ChatGPT's Secure MCP
Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) and
[Claude custom
connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).
Prefer testing the tunnel with the existing stdio server if account access
supports it; otherwise specify an authenticated HTTPS route for the chosen
client. Do not assume an OpenAI tunnel is usable by Claude.

Acceptance:

- Write a short deployment decision with the chosen client, supported
  connection/authentication method, required account permissions, and where the
  service will run.
- Use a synthetic vault for the first connectivity check. For a tunnel, verify
  tool discovery through the actual ChatGPT connection and verify which
  workspace principals have access. For HTTP, first validate
  transport/authentication using a local test client; the real web-client check
  follows deployment.
- Determine whether the chosen client needs additional search/fetch schemas or
  citation URLs for the intended feature. Add a thin compatibility adapter only
  if required; preserve the four core tool contracts. Distinguish normal tool
  calling from specialized deep-research integration.
- Record missing external prerequisites explicitly. Do not implement a custom
  OAuth server or purchase infrastructure to bypass an unresolved decision.

## Task 8 — Build the selected remote runtime

Depends on: Task 7's concrete deployment decision.

For a tunnel route, prepare a supervised tunnel-client/server service and its
secret/configuration handling. For a shared HTTPS route, add the SDK's
Streamable HTTP adapter and the selected supported authentication integration.
Package for Unraid when making the service permanent: non-root process,
read-only vault mount, configuration/secrets outside Git, and startup/health
diagnostics. Choose a dedicated working tree or materialized snapshot and
document an external synchronization procedure.

Acceptance:

- The same core behavior passes through the chosen transport; stdio continues
  to work.
- Invalid, expired, and unauthorized credentials/workspace access fail at the
  relevant boundary. For HTTP, verify token validation and discovery, TLS
  routing, and Origin/Host handling as applicable; do not treat a hidden URL as
  authentication.
- Runtime cannot write to the mounted vault. Restart/reconnection works without
  corrupting data or requiring a new index.
- Rate/concurrency limits and safe logging are configured for remote requests.
- Prepare exact launch/deployment configuration, synthetic verification
  evidence, and rollback instructions before any separately required deployment
  approval.

## Task 9 — Connect the web client and verify daily use

Depends on: Task 8 and authorization to activate the selected
deployment/account connection.

Deploy the prepared service and register it using the chosen client's supported
flow. First confirm synthetic retrieval; then connect the intended vault scope
and repeat representative questions. Keep credentials and private excerpts out
of the repository.

Acceptance:

- The actual web client discovers tools, finds a known note, reads its relevant
  content, and provides a useful source citation.
- Disconnected/revoked access stops working; restart and ordinary
  synchronization recover cleanly.
- Record the operational runbook: start/stop, update/rollback, sync, credential
  rotation, and connection troubleshooting.
- Declare completion for the tested client only. Connecting the other provider
  is a follow-up with its own connectivity/authentication checks.

## Process maintenance — Review orchestration efficiency

This is an ongoing, non-blocking maintenance item rather than a product
implementation task. Review the orchestration workflow after the next two or
three substantial orchestrated tasks, and repeat the review when the same
friction appears across multiple runs. Do not delay the next implementation
task unless the review exposes a correctness, security, or privacy risk.

Use evidence from actual runs: elapsed time, delegated-agent count, retries,
duplicate repository inspection or validation, weak handoffs, blocked commands,
and which internal or external review findings changed the result. Inspect the
repository skill, agent assignments, helper scripts or CLI invocations,
model-role choices, validation ownership, waiting behavior, and sandbox or
approval handling.

Update [the orchestration
skill](../.agents/skills/orchestrated-implementation/SKILL.md) and its
references only when the evidence supports a concrete improvement. Keep private
prompts, logs, credentials, note contents, and personal paths out of the
repository; record sanitized conclusions only.

Review outcome:

- Repeated inefficiencies and their causes are recorded without private data.
- Any changed workflow rule has a demonstrated reason and reduces a specific
  cost or risk.
- The skill-creator validator (`quick_validate.py`) and documentation checks
  pass after changes.
- A correctness or privacy problem can justify a new rule after one incident;
  efficiency friction becomes a rule only after it recurs.

## Workflow improvement plan

This plan, agreed on 2026-09-26, moves repeated mechanical steps into
scripts and matches delegation to each task's risk. Each stage is a separate
change. Mark a stage **(complete)** in the change that completes it, and revise
later stages when earlier work shows they need to change.

Stages 1–6 are complete: `scripts/check` and the CI workflow exist (see
[AGENTS.md](../AGENTS.md)), `tests/conftest.py` removes `KNOWLEDGE_ROOT` from
the test environment, and Task 3's read semantics were decided and implemented.
The fixture does not isolate the filesystem, so Task 5 tests must still pass
the synthetic root explicitly. Task 3 is the comparison baseline for Stage 6:
the coordinating agent implemented it directly, and at the vault owner's
request GPT-6 Sol reviewed it; the review found a symlink race that was fixed.

5. **Orchestration skill rewrite (complete).** The
   [skill](../.agents/skills/orchestrated-implementation/SKILL.md) describes
   roles by responsibility, so either a Claude or a Codex model may
   coordinate. It was tested on 2026-09-29 by one real review run in each
   direction. What later work needs to know:
   - Independent reviews and other consultations with the other model family
     run only when the vault owner requests them; a plan entry is not a
     request. The coordinating agent reports risks that tests may miss. Since
     Stage 6, it may delegate to its own family without being asked.
   - `references/cross-family.md` holds the model identifiers and the tested
     `codex exec` and `claude --print` commands, with their deadlines, result
     checks, and resume by ID. `references/review.md` holds the review
     procedure.
   - A Codex agent in the read-only sandbox cannot run pytest, and a Codex
     writer cannot use uv, so the coordinating agent prepares `.venv` and runs
     the full validation.
   - `AGENTS.md` tells agents to read repository skills from
     `.agents/skills/`. The repository has no tool-specific directories; a
     developer may add a local `.claude/skills/orchestrated-implementation`
     link and exclude it through `.git/info/exclude`.

6. **Process review (complete).** Reviewed on 2026-09-29 from the Task 3 and
   Task 4 reports. The coordinating agent may now delegate to its own model
   family without being asked; consultations and reviews with the other
   family still need the vault owner's request. `references/review.md` now
   requires sending a proposed fix back to the reviewer before applying it or
   asking the vault owner to approve it.

Starting assignments for the remaining Phase 1 tasks. Adjust them when a task
turns out simpler or riskier than expected:

| Task | Assignment |
|---|---|
| 4 | One investigator for ripgrep behavior. |
| 5 | The coordinating agent reads the installed SDK code first and adds an investigator only if questions remain. Focus on cancellation across the adapter/core boundary. |
| 6 | The coordinating agent, plus the vault owner's manual check in a real MCP host. |

## Reusable prompt for a coding session

```text
Implement Task <N> from docs/implementation-tasks.md only, following
AGENTS.md. Read docs/implementation-plan.md and docs/phase-1-contract.md
first. Use the orchestrated-implementation skill when delegating work.
If a contract contradiction blocks implementation, identify it rather than
silently changing semantics. Stop at this task boundary; do not implement
later milestones or deploy anything.
```

Keep one coherent diff per task. Commit only when requested or already
authorized in that implementation session. The important boundary is a
reviewable change with passing checks, not a prescribed number of lines.
