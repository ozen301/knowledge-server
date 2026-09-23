---
name: orchestrated-implementation
description: Coordinate substantial repository implementation with a strong owner, bounded read-only investigators, one writer at a time, owner validation, and optional independent review. Use when the user requests multi-agent orchestration or an agreed task has enough uncertainty, risk, or parallel research to justify delegation. Do not use for small localized changes.
---

# Orchestrated Implementation

Use the repository's `AGENTS.md`, specification, and task definition as the
source of truth. The coordinating agent remains responsible for scope,
correctness, validation, and the final report; delegation does not transfer
that responsibility.

## Decide whether delegation helps

Delegate only when it can reduce uncertainty or wall-clock time. Good reasons
include independent research questions, an unfamiliar dependency or API,
multiple separable subsystems, or a requested second-opinion review. Handle a
small documentation edit, localized fix, or straightforward scaffold directly.

When available, prefer GPT-6 Sol (`gpt-6-sol`) as an economical owner or
writer, GPT-6 Astra (`gpt-6-astra`) for demanding ownership, and GPT-6 Luna
(`gpt-6-luna`) for narrow read-only investigation. These are repository role
preferences, not measured project benchmarks. Treat them as preferences, not
requirements: preserve an exact user model choice, verify exposed tool IDs, and
never silently substitute an unavailable model. See the [OpenAI changelog](https://developers.openai.com/api/docs/changelog).

## Assign bounded roles

- The owner reads the governing files, defines acceptance criteria, resolves
  conflicts, inspects the final diff, and runs the final validation. Only the
  owner commits, and only when the user has authorized a commit.
- Use at most two read-only investigators by default. Give each a distinct
  question, exact files or APIs to inspect, and a required evidence-based
  handoff. Run independent investigations in parallel.
- State in every assignment that `AGENTS.md` applies, and repeat the relevant
  constraints so agents with limited context cannot miss them. For this
  repository, include the read-only vault, privacy, and commit rules.
- Use one writing agent at a time in the same working tree as the owner. Give it
  the agreed scope, relevant specification, tests to add first, allowed files,
  acceptance checks, and a prohibition on committing. Require it to preserve
  unrelated changes.
- Add an independent reviewer only when the user requests one or when the
  change is sufficiently critical to justify its cost. Review follows owner
  validation rather than replacing it.

## Run the workflow

1. Inspect repository instructions, the agreed task, relevant specifications,
   current status, and existing changes. State the implementation boundary.
2. Identify only the uncertainties that materially affect implementation.
   Delegate non-overlapping research when doing so is useful.
3. Synthesize the research before starting a writer. Resolve contradictions
   yourself and record any decision that still needs the user.
4. Give one writer a self-contained assignment. Require the repository's
   Spec -> Tests -> Implementation -> Validation -> Drift prevention sequence.
5. Inspect the writer's diff and claims. Do not accept a handoff merely because
   its checks passed; confirm scope, behavior, tests, and documentation.
6. Run focused checks during development and the full required validation once
   the change is stable. Repeat checks only after relevant changes or while
   diagnosing a failure.
7. If independent review is warranted, make it read-only and verify every
   finding against the source before acting. For an external CLI reviewer, read
   [references/external-review.md](references/external-review.md).
8. Report changes, rationale, validation, unresolved work, and review results.
   Commit only if authorized, using a focused Conventional Commit.

## Keep orchestration efficient

- Give agents narrow questions and preselected context instead of asking each
  one to rediscover the whole repository.
- Batch independent investigations. Do not create multiple agents for the same
  question unless diversity itself is the goal.
- Avoid simultaneous writers and avoid asking a reviewer to repeat validation
  that the owner can report as evidence.
- Centralize dependency synchronization and full validation in the owner.
  Investigators do not initialize dependencies; writers reuse the prepared
  environment for focused checks unless their task changes dependencies.
- When a required package-manager command cannot use its standard cache or
  runtime directory because of the sandbox, rerun that same command with the
  narrow approval it needs. Do not change project configuration or redirect
  persistent caches and managed runtimes into `/tmp` solely to bypass agent
  permissions.
- Require each handoff to contain: outcome, supporting file paths or API
  signatures, files changed, checks run, and unresolved risks.
- Give delegated work a clear stopping condition. If an agent is blocked, the
  owner should diagnose, take over, or reassign once rather than creating an
  unbounded retry chain.
- Before using an unfamiliar CLI, inspect its help and choose an explicitly
  non-interactive invocation. Keep progress observable; investigate a silent
  process instead of repeatedly waiting without new evidence.
- After substantial runs, record only material orchestration failures or gains.
  Revise this skill when patterns recur, not after isolated incidents.
