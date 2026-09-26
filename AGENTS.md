# Repository guide

This project gives agents access to a personal knowledge base through the Model
Context Protocol (MCP). The [implementation plan](docs/implementation-plan.md)
describes the architecture and development stages. The
[specification](docs/phase-1-contract.md) defines the first version's tool
behavior, and the [implementation tasks](docs/implementation-tasks.md) list the
work and the checks required to complete it.

## Working rules

- Read the relevant files before editing. Check code and test results before
  claiming that a feature works.
- Work within the scope the user has authorized, including authorization given
  earlier. An implementation request covers the necessary edits, tests, and
  documentation; a review request covers inspection and reporting, not fixes.
  Approval for one change does not extend to unrelated or later work, and a
  general acknowledgement or a request for next steps does not expand the
  authorized scope.
- Do not overwrite unrelated uncommitted changes. Edit only the files required
  by the task.
- Do not edit the real knowledge vault or run Git commands that change its
  files or repository state. The vault owner manages synchronization.
- Keep credentials, personal note contents, private reference documents,
  runtime state, and logs out of commits and agent review handoffs.
- Test with small collections of invented notes. Automated tests must run
  without the real vault, external accounts, network services, or a local
  `.env`.
- Treat another agent's review as claims to verify against the repository and
  agreed requirements. Fix confirmed issues when fixes are within the
  authorized scope; otherwise report them. Explain rejected findings and name
  claims you could not verify.

## Workflow

Follow **Spec -> Tests -> Implementation -> Validation -> Drift prevention**
for features and behavior changes, including bug fixes:

1. **Spec:** Read the specification. Before writing dependent tests or code,
   resolve decisions left open for the vault owner; for a new material
   ambiguity, present options with a recommendation and wait for the decision.
   Write behavior the request already establishes into the specification
   without asking again. Routine implementation details remain the agent's
   choice.
2. **Tests:** Before implementation, add tests for new behavior. For a bug fix,
   first confirm that a test fails because of the bug.
3. **Implementation:** Write the code to satisfy the specification and tests.
4. **Validation:** Run the checks below and inspect the diff.
5. **Drift prevention:** Update the specification, this guide, and usage
   instructions when a change makes them inaccurate. Include those updates in
   the same change as the code and tests.

An authorized task does not require separate approval for each workflow step,
but it does not by itself authorize commits or pushes.

When you finish a task, tell the user what changed, why, and which checks
passed. Identify any failed checks, checks you could not run, and unfinished
work.

## Implementation conventions

- Use uv, `pyproject.toml`, and `uv.lock` for Python dependencies.
- Use the official MCP Python SDK v2. Check imports and API signatures against
  the installed SDK or its matching official documentation.
- Use the dependencies specified by the implementation task. Explain any
  additional dependency and why the existing tools cannot meet the requirement.
- Put application code under `src/knowledge_server/`. Core modules must not
  import the MCP SDK.
- Reuse the shared path and visibility policy
  (`src/knowledge_server/core/paths.py`) in every tool, and apply shared
  content and size checks consistently where required by the contract.
- Add parameter and return type hints to module-level functions and public
  methods.
- Target Python 3.14 or later. Use its native annotation behavior and current
  standard-library features; do not add compatibility code such as
  `from __future__ import annotations` or backport packages unless a
  requirement calls for it.
- Use pytest for tests, Ruff for formatting and lint checks, and Pyright for
  type checks.
- Send diagnostic messages to stderr. Stdout carries MCP messages, so other
  output can break communication. Never log secrets, note contents, or queries.

## Validation

After code or configuration changes, run `scripts/check`. It runs the
commands below, checks untracked text files for trailing whitespace, and
summarizes the results:

```sh
uv sync --locked --dev
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
git diff --check
git diff --cached --check
```

CI runs `scripts/check --ci` on pushes to `main` only, so validate `dev`
locally. For documentation-only changes, check whitespace, links, claims, and
examples. Repeat checks only after further changes or when investigating a
failure.

## Documentation

Documentation is read by developers who are proficient users of English as a
second language and may be new to the project. Write to be accurate,
reader-friendly, coherent, and succinct, and let those goals instead of a fixed
pattern determine structure and style:

- **Accurate:** claims are true for the document's role. Usage instructions
  describe implemented behavior; plans and specifications describe intended
  behavior. Identify features that are not implemented, and mark proposals and
  open decisions clearly.
- **Reader-friendly:** give the context the reader needs to understand and act.
  Keep precise technical terms, but spare the reader needless linguistic
  effort.
- **Coherent:** ideas connect logically, the structure fits the content, and
  terms stay consistent across documents.
- **Succinct:** cut words and repetition that do not help the reader; keep the
  context, explanations, and summaries that do.

When these goals conflict, accuracy comes first, and brevity never justifies
ambiguity or omitting necessary information. Simplified Technical English is a
useful reference for procedures and warnings.

Conventions:

- Give instructions in execution order. Specify the files, commands, and
  expected results needed to follow them.
- Use ASCII `->` for arrows.
- When documenting implemented code, describe its current behavior. Mention
  history, comparisons, or unchanged behavior only when they explain a decision
  or help the reader.
- Use established technical terms and the names defined in
  [CONTEXT.md](CONTEXT.md). Explain unfamiliar concepts, and avoid unnecessary
  coined labels and misleading analogies, such as calling a task list "task
  cards."
- Avoid filler and canned conclusions such as "leverage," "it's worth noting,"
  "Bottom Line," and "In short."
- Keep detailed task progress in `docs/implementation-tasks.md`; other
  documents link to it. The README may summarize the capabilities available
  now.
- Wrap prose at about 80 columns. Tables, code, and long URLs are exempt.

## Docstrings and comments

Apply the same guidance to docstrings and comments. In docstrings, explain what
a function or class does and any inputs, return values, or errors that a caller
needs to understand. In comments, explain reasons or constraints that the code
does not make clear. Update both when the code's behavior changes.

## Commits

Commit and push only when the user has authorized those actions for the
relevant scope. Use
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) for
commit messages: `type: description` or `type(scope): description`. Omit the
body for straightforward changes; add one when the reason or consequences need
explanation beyond the subject. Do not add co-author trailers or other agent
signatures to commit messages or pull request descriptions.
