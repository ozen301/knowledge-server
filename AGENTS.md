# Repository guide

This project gives agents access to a personal knowledge base through the
Model Context Protocol (MCP). The [implementation plan](docs/implementation-plan.md)
describes the architecture and development stages. The
[specification](docs/phase-1-contract.md) defines the first version's tool
behavior, and the [implementation tasks](docs/implementation-tasks.md) list
the work and the checks required to complete it.

## Working rules

- Read the relevant files before editing. Check code and test results before
  claiming that a feature works.
- Do not overwrite unrelated uncommitted changes. Edit only the files required by the task.
- The Phase 1 server is read-only. Do not edit the real knowledge vault or
  run Git commands that change its files or repository state; the owner manages synchronization.
- Keep credentials, personal note contents, private reference documents,
  runtime state, and logs out of commits and agent review handoffs.
- Test with small collections of invented notes. Automated tests must run without the
  real vault, external accounts, network services, or a local `.env`.

## Workflow

Follow **Spec -> Tests -> Implementation -> Validation -> Drift prevention**
for features and behavior changes, including bug fixes:

1. **Spec:** Read the specification. Add any missing behavior required by the task before writing code.
2. **Tests:** Before implementation, add tests for new behavior. For a bug fix, first confirm that a test fails because of the bug.
3. **Implementation:** Write the code to satisfy the specification and tests.
4. **Validation:** Run the checks below and inspect the diff.
5. **Drift prevention:** Update the specification, this guide, and usage instructions when a change makes them inaccurate. Include those updates in the same change as the code and tests.

An agreed task does not require separate approval for each step.

## Implementation conventions

- Use uv, `pyproject.toml`, and `uv.lock` for Python dependencies.
- Use the official MCP Python SDK v2. Check imports and API signatures against
  the installed SDK or its matching official documentation.
- Use the dependencies specified by the implementation task. Explain any
  additional dependency and why the existing tools cannot meet the requirement.
- Put application code under `src/knowledge_server/`. Core modules must not import the MCP SDK.
- Use the same file-access checks in all tools.
- Add parameter and return type hints to module-level functions and public methods.
- Target Python 3.14 or later. Use native annotation behavior and current stable
  standard-library features; do not add compatibility scaffolding unless a
  requirement calls for it.
- Use pytest for tests, Ruff for formatting and lint checks, and Pyright for type checks.
- Send diagnostic messages to stderr. Stdout carries MCP messages, so other
  output can break communication. Never log secrets, note contents, or queries.

## Validation

After code or configuration changes, run the commands below:

```sh
uv sync --locked --dev
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
git diff --check
git diff --cached --check
```

Check whitespace in new text files too; the Git commands above omit untracked files.
For documentation-only changes, check whitespace, links, claims, and examples.
Repeat checks only after further changes or when investigating a failure.

When you finish a task, tell the user what changed, why, and which checks
passed. Identify any failed checks, checks you could not run, and unfinished work.

## Documentation

Write for human developers who are proficient users of English as a second
language but may be new to the project. Introduce the purpose and necessary
concepts before implementation details, and develop explanations in coherent
paragraphs that show how each idea relates to the next.

Use natural English with enough detail to preserve technical accuracy and
explain the reasoning. Vary sentence length and structure to suit the content;
keep related ideas together when splitting them would make the prose abrupt.
Simplified Technical English can help clarify difficult technical passages,
but it is an optional reference, not the required style for all documentation.

- Give instructions in execution order. Specify the files, commands, and
  expected results needed to follow them.
- Use ASCII `->` for arrows.
- Usage instructions describe implemented behavior. Plans and specifications
  describe intended behavior. Identify features that are not implemented.
- When describing code, state its current behavior instead of its previous
  behavior. Do not emphasize what remains unchanged, or announce actions you
  will not take.
- Use commonly recognized technical terms and consistent names. Explain
  specialized concepts in plain English. Do not use obscure jargon, invented
  terminology, or misleading analogies such as calling a task list "task cards."
- Avoid filler and canned conclusions such as "leverage," "it's worth noting,"
  "Bottom Line," and "In short."
- State actions directly. Include technical details and comparisons when they
  help the reader understand or perform the task.

## Docstrings and comments

Apply the same guidance to docstrings and comments. In docstrings, explain what a
function or class does and any inputs, return values, or errors that a caller
needs to understand. In comments, explain reasons or constraints that the
code does not make clear. Update both when the code's behavior changes.


## Commits

Commit only when authorized by the user. Use
[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/)
for commit messages: `type: description` or `type(scope): description`.
Omit the body for straightforward changes; add one when the reason or
consequences need explanation beyond the subject.
