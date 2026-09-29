---
name: orchestrated-implementation
description: Coordinate a repository task across agents - read-only investigators, one writer at a time, validation and reporting by the coordinating agent, and an independent review from another model family when the user asks for one. Works when either a Claude or a Codex model coordinates.
---

# Orchestrated Implementation

`AGENTS.md`, the specification, and the task entry govern the work. The
coordinating agent remains responsible for scope, correctness, validation, and
the final report.

## Roles

- **Coordinating agent:** states the task boundary, resolves decisions that
  belong to the vault owner before dependent tests or code, inspects every
  diff, runs the final validation, and reports. Only the coordinating agent
  commits, and only when the user has authorized it.
- **Investigators** answer distinct read-only questions with evidence. Run
  independent investigations in parallel.
- **One writer at a time** works in the shared working tree. This includes the
  coordinating agent: it does not edit while a delegated writer works.
- **Reviewers** follow [references/review.md](references/review.md).

## Models

- An explicit user choice of model always applies. Never substitute an
  unavailable model silently; tell the user and choose again.
- Choose within your own family unless the task calls for another. Prefer a
  writer that costs less than you and can do the task; you may also implement
  directly.
- For models and commands of the other family, see
  [references/cross-family.md](references/cross-family.md).

## Assignments and handoffs

An assignment states its objective, the task and specification sections that
apply, the allowed files, the tests to write first (for a writer), and a
stopping condition. It says that `AGENTS.md` applies. The handoff reports the
outcome, evidence (file:line or API signatures), files changed, checks run,
and open risks.

Inspect each diff and claim yourself; passing checks alone do not make a
handoff acceptable. If an agent is blocked, diagnose, take over, or reassign
once instead of retrying without limit.

## Validation and review

The coordinating agent synchronizes dependencies and runs the full
validation. Investigators do not install dependencies; writers reuse the
prepared environment unless their task changes dependencies.

When a package-manager command cannot use its standard cache or runtime
directory because of the sandbox, rerun the same command with the narrow
approval it needs. Do not change project configuration or move persistent
caches or managed runtimes into `/tmp` only to avoid agent permissions.

An independent review runs when the user asks for one. If you see a risk that
tests may miss, such as weakened path or visibility checks, process cleanup,
or unverified SDK behavior, say so in the report.

## Changing this skill

A correctness or privacy problem can justify a new rule after one incident;
efficiency friction must recur before it becomes a rule.
