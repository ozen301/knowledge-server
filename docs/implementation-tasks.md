# Implementation tasks

Run these sequentially. Tasks 1 and 2 are complete: the package scaffold, SDK
compatibility smoke check, and shared Phase 1 configuration/models/path policy
are implemented. Tasks 3–6 cover the remaining Phase 1 work; Task 6a records the retrieval evaluation before web integration. Tasks 7–9
outline the user's next priority, web access; finalize route-specific details
in Task 7 before coding or deploying that integration. Define tasks for
retrieval upgrades from the evaluation findings. The
[workflow improvement plan](#workflow-improvement-plan) runs alongside Tasks
3 and 4; check its status before starting each task.

For each task, follow **Spec -> Tests -> Implementation -> Validation -> Drift prevention** as defined in [AGENTS.md](../AGENTS.md). Establish the behavior and acceptance checks first, express meaningful behavior in tests before implementing it, and finish by checking that the specification, tests, code, and usage instructions agree.

## Task 1 — Scaffold and verify SDK compatibility

Depends on: no earlier task.

Read the plan and contract. Create the Python package, `pyproject.toml`, `uv.lock`, test setup, Ruff configuration, and Pyright configuration with `typeCheckingMode = "basic"`. Set `requires-python = ">=3.14"` and configure Pyright for Python 3.14, checking application code and tests. Pin the development interpreter through uv; it may be newer than the minimum. Add a short development-command section to README. Keep runtime dependencies to the official MCP SDK and any directly used modeling dependency; no vector/database/web packages.

Use a synthetic, temporary SDK smoke check to verify the installed v2 imports and stdio server startup/shutdown. Do not register placeholder production tools. Record the resolved SDK version and supported invocation rather than copying unverified tutorial code.

Acceptance:

- A clean `uv sync --locked --dev` succeeds.
- Package import, a basic packaging smoke test, Ruff, and type checking pass.
- `uv run --python 3.14 --locked --dev pytest` passes with a uv-managed Python 3.14 interpreter, confirming the scaffold supports the declared minimum.
- Include the generated lockfile in the task's changes; the project installs without reading a real vault.
- README clearly distinguishes available commands from features still unimplemented.

Non-goals: tool implementation, Docker, HTTP, real-vault access.

## Task 2 — Configuration, data models, and shared path policy

Depends on: Task 1.

Implement config, request/result models, domain errors, and path validation/discovery. Define the input/output models from the contract for reuse by later tasks. Define each initial limit once and use those definitions in both request validation and operations. These values are enforced now and reviewed in Task 6a. Centralize visibility and filesystem checks. The confirmed scope is all non-hidden Markdown; do not add folder allowlist configuration now.

Acceptance:

- Missing/invalid root fails clearly. Relative API paths work with spaces and Unicode.
- Parameterized tests cover absolute/traversal/hidden/symlink/special-file rejection, extension rules, root-prefix collisions, and directory normalization.
- Discovery and direct access agree on eligibility, including Git-ignored Markdown.
- Denial/errors never contain the outside sentinel contents or server-generated absolute paths.
- Core modules import without importing MCP. No tool can override the configured root.

Review checkpoint: inspect containment checks and symlink handling before building on them. This is a code review, not a new permission gate.

Non-goals: subprocesses, MCP decorators, user identities or role systems.

## Task 3 — Bounded read, list, and metadata

Depends on: Task 2.

Resolve the Task 3 BOM/newline and `next_line` questions listed in the contract
before writing tests.

Implement shared bounded UTF-8 loading and the three core operations in `reader.py`. Derive hashes and line counts from the same loaded bytes. Do not cache source content.

Acceptance:

- Read ranges, EOF, empty files, CRLF/BOM, byte-limit continuation, invalid encoding, oversized files, and huge single lines follow the contract.
- Listing applies policy before stable sorting and pagination; offset/limit edge cases work.
- Info returns useful metadata for oversized/invalid-text files without an unbounded read.
- A changed fixture is reflected in a subsequent call; hashes change with bytes.
- A file removed before opening produces `NOT_FOUND`; a permission failure produces `ACCESS_DENIED`. Use controlled fixtures or injected I/O failures so these checks also work under privileged test runners.
- Synthetic vault bytes and directory contents remain unchanged after all operations.

Non-goals: Markdown parsing, frontmatter extraction, Obsidian link resolution, Git metadata.

## Task 4 — Literal search through ripgrep

Depends on: Tasks 2 and 3.

Resolve the Task 4 snippet-window question listed in the contract before writing
tests.

Implement recursive discovery and controlled ripgrep execution. Reuse the same visibility and content rules as reading. Keep sorting, snippets, truncation, skip counts, and failure semantics deterministic. Break helpers into readable functions if necessary; do not introduce a general subprocess framework.

Acceptance:

- Real-ripgrep integration tests find expected lines in synthetic English and CJK notes, including phrases, punctuation, leading `-`, filenames with spaces, and repeated matches on one line.
- Case handling, empty/no-match results, exact hit-limit boundaries, and snippet windows follow the contract.
- Invented English and Japanese examples demonstrate that different Unicode representations can miss, including `é` versus `e` plus a combining accent and `が` versus `か` plus a combining voiced mark. Label these as limitations pending NFC support. Test successful matches with identical representations too.
- During recursive search, a discovered file removed before reading increments `skipped.unreadable` and sets `incomplete`; the same failure on an explicit file target returns `NOT_FOUND`.
- A matching secret in hidden/outside/symlinked content never appears.
- Tests verify `shell=False`, argument separation, user-config isolation, exit code 1, process errors, timeout/cancellation cleanup, and output/work budgets.
- Tests prove huge subprocess output is bounded while being read, not merely after full capture.
- Observe latency on the small fixture set and record it; no elaborate benchmark suite yet.

Review checkpoint: inspect actual process arguments, cleanup, candidate filtering, and budget enforcement.

Non-goals: regex, AND/OR query syntax, ranking, FTS, background indexing.

## Task 5 — Expose the four tools over stdio

Depends on: Tasks 3 and 4.

Implement the entry point and thin MCP wrappers in `src/knowledge_server/adapter/server.py`. Generate schemas from the typed models and use SDK support for successful structured results and their JSON text representation. Add clear descriptions, read-only/destructive annotations as appropriate, and shared translation of domain errors and unexpected application exceptions. Handle blocking work without blocking the async protocol loop; cancellation must reach any active search process. Use SDK transport/version handling, not handwritten JSON-RPC.

Acceptance:

- A real SDK client starts the server subprocess against a temporary vault, discovers exactly four tools, calls each successfully, and shuts it down cleanly.
- Discovered input/output schemas agree with the contract; invalid arguments and domain errors are distinguishable from successful empty results.
- An unexpected exception in each tool produces a safe `INTERNAL_ERROR` with `isError=true`. Inject exceptions containing an absolute path and invented private text; responses expose neither, and logs exclude the private text and query. A subsequent valid call succeeds.
- Successful structured content and its JSON text representation contain equivalent values.
- Read-only annotations are present; policy enforcement is tested independently of those annotations.
- Protocol stdout contains only protocol output. Diagnostic logging does not break tool calls.
- A failed call does not prevent the next valid call. No HTTP listener opens.

Non-goals: MCP resources/prompts, client sampling, custom protocol negotiation, hosted endpoints.

## Task 6 — Local integration, documentation, and release check

Depends on: Task 5.

Document the launch command and environment setup using portable example paths. Replace these paths with local absolute paths when registering the host. Planned form, to verify against the implemented entry point:

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

- Complete all validation commands in [AGENTS.md](../AGENTS.md), including checks for staged changes and new files.
- Run the complete suite on the minimum supported version with `uv run --python 3.14 --locked --dev pytest`.
- A synthetic end-to-end question produces a search hit, a read of the cited lines, and a correct source path in the host's answer.
- Measure search/read timings against the invented notes and record usability gaps without setting a performance guarantee from this small sample.
- Confirm the server makes no vault writes or Git changes. No network deployment is part of this task.
- If host access is unavailable, report exactly that manual check as pending; automated protocol tests alone do not complete the real-host acceptance gate.

The local MVP is complete only after these checks and the real-host workflow pass. Do not claim future milestones are complete because the scaffolding could support them.

## Task 6a — Record repeatable retrieval evaluation

Depends on: Task 6. This implements milestone 2.

Create `docs/retrieval-evaluation.md` with a small predefined question set and expected source notes. Store the invented notes in `tests/fixtures/vault/`, reusing suitable existing fixtures. Include English and Japanese examples, questions with no answer in the notes, and the known Unicode matching limitation. Record the natural-language question separately from the literal search queries so a failed result can be traced to query choice, matching, reading, or citation.

Acceptance:

- Each example names its invented source notes and expected answer or no-answer outcome. Run the questions through the local host and record the actual queries, retrieved paths, and whether the cited text supports the answer.
- Add repeatable search/read assertions to the automated tests for the literal queries and expected results. Tests depend only on the invented notes, not a model provider or personal vault.
- Record failures and their causes in the evaluation document. Use these examples to compare future retrieval changes.
- Review the initial limits against the observed results. Record whether adjustments are justified; apply any chosen adjustment to the code, contract, and tests together.
- Record NFC-equivalent matching as an important deferred feature and assess the demonstrated missed matches before proposing broader search upgrades. Its implementation requires a specification for normalized search input and original-text snippets; width matching remains a separate decision.

The owner may also evaluate personal notes privately. This is optional; keep private questions, excerpts, paths, and results outside committed files and automated fixtures.

## Task 7 — Choose and validate the first web-client route

Depends on: Task 6a and access to the selected client account.

Confirm which web client to connect first and inspect its available developer/connector settings. These details can wait until this task. Recheck the current official documentation for [ChatGPT's Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) and [Claude custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp). Prefer testing the tunnel with the existing stdio server if account access supports it; otherwise specify an authenticated HTTPS route for the chosen client. Do not assume an OpenAI tunnel is usable by Claude.

Acceptance:

- Write a short deployment decision with the chosen client, supported connection/authentication method, required account permissions, and where the service will run.
- Use a synthetic vault for the first connectivity check. For a tunnel, verify tool discovery through the actual ChatGPT connection and verify which workspace principals have access. For HTTP, first validate transport/authentication using a local test client; the real web-client check follows deployment.
- Determine whether the chosen client needs additional search/fetch schemas or citation URLs for the intended feature. Add a thin compatibility adapter only if required; preserve the four core tool contracts. Distinguish normal tool calling from specialized deep-research integration.
- Record missing external prerequisites explicitly. Do not implement a custom OAuth server or purchase infrastructure to bypass an unresolved decision.

## Task 8 — Build the selected remote runtime

Depends on: Task 7's concrete deployment decision.

For a tunnel route, prepare a supervised tunnel-client/server service and its secret/configuration handling. For a shared HTTPS route, add the SDK's Streamable HTTP adapter and the selected supported authentication integration. Package for Unraid when making the service permanent: non-root process, read-only vault mount, configuration/secrets outside Git, and startup/health diagnostics. Choose a dedicated working tree or materialized snapshot and document an external synchronization procedure.

Acceptance:

- The same core behavior passes through the chosen transport; stdio continues to work.
- Invalid, expired, and unauthorized credentials/workspace access fail at the relevant boundary. For HTTP, verify token validation and discovery, TLS routing, and Origin/Host handling as applicable; do not treat a hidden URL as authentication.
- Runtime cannot write to the mounted vault. Restart/reconnection works without corrupting data or requiring a new index.
- Rate/concurrency limits and safe logging are configured for remote requests.
- Prepare exact launch/deployment configuration, synthetic verification evidence, and rollback instructions before any separately required deployment approval.

## Task 9 — Connect the web client and verify daily use

Depends on: Task 8 and authorization to activate the selected deployment/account connection.

Deploy the prepared service and register it using the chosen client's supported flow. First confirm synthetic retrieval; then connect the intended vault scope and repeat representative questions. Keep credentials and private excerpts out of the repository.

Acceptance:

- The actual web client discovers tools, finds a known note, reads its relevant content, and provides a useful source citation.
- Disconnected/revoked access stops working; restart and ordinary synchronization recover cleanly.
- Record the operational runbook: start/stop, update/rollback, sync, credential rotation, and connection troubleshooting.
- Declare completion for the tested client only. Connecting the other provider is a follow-up with its own connectivity/authentication checks.

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

Update [the orchestration skill](../.agents/skills/orchestrated-implementation/SKILL.md)
and its references only when the evidence supports a concrete improvement.
Keep private prompts, logs, credentials, note contents, and personal paths out
of the repository; record sanitized conclusions only.

Review outcome:

- Repeated inefficiencies and their causes are recorded without private data.
- Any changed workflow rule has a demonstrated reason and reduces a specific
  cost or risk.
- The skill validator and documentation checks pass after changes.
- One-off incidents do not become permanent requirements without broader
  evidence.

## Workflow improvement plan

This plan, agreed on 2026-09-26, moves repeated mechanical steps into
scripts, matches delegation to each task's risk, and records enough evidence
to judge the workflow. Each stage is a separate change. Mark a stage
**(complete)** in the change that completes it, and revise later stages when
earlier work shows they need to change.

Order: Stage 1 -> Stage 2 -> Stage 3 -> Stage 4. Stages 5 and 6 wait until
Task 4 is close.

1. **Validation script and CI.** `scripts/check` and the GitHub Actions
   workflow exist; see the validation section of [AGENTS.md](../AGENTS.md).
   Pending: confirm that the first CI run on `main` passes.

2. **Test environment fixture (complete).** `tests/conftest.py` removes
   `KNOWLEDGE_ROOT` from the test environment. It does not isolate the
   filesystem; Task 5 tests must still pass the synthetic root explicitly.

3. **Settle the Task 3 read semantics.** The contract already specifies
   normalized newlines, hashes of the raw bytes, and `next_line` as the next
   unread line. Resolve only the remaining ambiguity with the owner, using
   concrete input and output examples. Proposed defaults: remove a leading BOM
   from returned text, and return `next_line` whenever more content follows,
   even after a fully satisfied range. Do not add result fields. Then remove
   the "Before Task 3" note from the contract. The Task 4 snippet question
   stays open until Task 4.

4. **Task 3 without delegation.** The owner implements Task 3 directly. This
   run is a baseline for comparison, not evidence about whether delegation
   works.

5. **Review preparation before the Task 4 review checkpoint.**
   - Add `references/assignment-template.md` to the orchestration skill. It
     contains the objective, a task reference with only the criteria specific
     to the assignment, allowed files, tests to write first, the stopping
     condition, the handoff format (outcome, evidence, files changed, checks
     run, open risks), and a short fixed block with the vault, privacy, and
     commit constraints. `AGENTS.md` remains the canonical source of
     repository rules. Shorten the reusable prompt below so it refers to
     `AGENTS.md` and the template.
   - Add `scripts/review.sh`, which builds the read-only external review
     command from verified CLI flags. It requires the model and effort as
     arguments. It records the CLI version, the model that ran, the reviewed
     revision, and a digest of the exact diff or snapshot it sent, including
     any untracked files that were explicitly selected. It limits retries,
     closes stdin, and restricts the reviewer's tools. Recheck the flags only
     when the CLI version changes.
   - In the skill, describe models by role (strong owner, economical writer,
     inexpensive investigator) and keep the exact model identifiers in one
     mapping. Keep the package-manager cache rule in `SKILL.md`, because it
     also applies during implementation.
   - Add an optional reproducer mode: the reviewer works in a separate
     worktree and supports each finding with the contract requirement and,
     when practical, a minimal reproducer. The owner confirms that it fails on
     the reviewed code and passes after the fix. Isolation from a hostile
     process running as the same OS user is out of scope (see the
     [implementation plan](implementation-plan.md)); ordinary concurrent
     changes, such as a file removed before opening, are in scope.
   - Add the rule for changing the skill: a correctness or privacy problem can
     justify a new rule after one incident, but efficiency friction must
     recur first.
   - The rewritten skill and reference meet these criteria:
     - The owner resolves decisions that need the user before dependent tests
       or code. The single-writer rule includes the owner: while a delegated
       writer works, the owner does not edit the shared working tree.
     - Required review checkpoints are honored. Additional independent review
       is allowed when a concrete risk justifies it.
     - Review inputs are accessible and stable: a stable snapshot of the exact
       code being reviewed, including explicitly selected uncommitted and
       untracked changes, and a prompt that names the governing files. Both
       reviewer CLIs are supported; switching providers is optional.
     - Reviews have a deadline. Raw output stays outside the repository. A
       failed or incomplete review is reported as such, never as "no
       findings."

6. **Closeout records and process review.** After each substantial task, add
   a record of about three lines to `docs/orchestration-log.md`: the task and
   revision; the agents and reviews used, with time (mark estimates); and what
   changed the result, such as accepted or rejected findings, rework, or
   blocked commands. After Tasks 3 and 4, use these records for the process
   maintenance review above. Two tasks can show friction but cannot support
   broad conclusions about models or delegation.

Starting assignments for the remaining Phase 1 tasks. Adjust them when a task
turns out simpler or riskier than expected:

| Task | Assignment |
|---|---|
| 3 | Owner only. |
| 4 | One investigator for ripgrep behavior, then an external review of process arguments, budgets, and cleanup, trying the reproducer mode. |
| 5 | The owner reads the installed SDK code first and adds an investigator only if questions remain. Focus on cancellation across the adapter/core boundary. |
| 6 | The owner, plus the manual check in a real MCP host. |

## Reusable prompt for a coding session

```text
Implement Task <N> from docs/implementation-tasks.md only.
Read docs/implementation-plan.md and docs/phase-1-contract.md first.
Inspect existing code and repository instructions before changing files.
Follow Spec -> Tests -> Implementation -> Validation -> Drift prevention.
Read the specification and add any missing behavior required by the task.
Write tests for new behavior before implementation. For a bug fix, first
confirm that a test fails because of the bug.
Reuse established models and helpers; preserve the documented behavior.
Use synthetic temporary vaults in tests; do not alter the real knowledge vault.
Implement the task's acceptance checks, run the relevant checks, and stop at
this task boundary. If a contract contradiction blocks implementation,
identify it rather than silently changing semantics.
Report changed files, checks run and results, and any unfinished acceptance
criteria. Update the specification and usage instructions to match the code.
Do not implement later milestones or deploy anything.
```

Keep one coherent diff per task. Commit only when requested or already authorized in that implementation session. The important boundary is a reviewable change with passing checks, not a prescribed number of lines.
