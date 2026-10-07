# Repository guide

This project gives agents access to a personal knowledge base through the Model
Context Protocol (MCP). The specifications, the [tool
contract](docs/tool-contract.md) and the [HTTP contract](docs/http-contract.md),
define exact behavior. The [design decisions](docs/design-decisions.md) explain
why the server works as it does, and the [roadmap](docs/roadmap.md) lists
optional later work and defines active tasks with their acceptance checks.

Repository skills are in `.agents/skills/<name>/SKILL.md`. When asked to use
one that your tool does not list, read that file and follow it. You may use the
orchestrated-implementation skill without being asked when delegating part of
a task is worth the handoff cost; the skill states the limits.

## Working rules

- Work within the scope the user has authorized, including authorization given
  earlier. An implementation request covers the necessary edits, tests, and
  documentation; a review request covers inspection and reporting, not fixes.
  Approval for one change does not extend to unrelated or later work, and a
  general acknowledgement or a request for next steps does not expand the
  authorized scope.
- Build the smallest change that meets the requirements and the project's
  context: one vault owner, maintained by hand. Prefer standard tools and
  vendor defaults over custom components. Treat every addition the
  requirements do not ask for, such as extra hardening, accounts, units,
  options, tests, or document sections, as a proposal: omit it, or list it
  with its cost and benefit for the vault owner to decide.
- Do not edit the real knowledge vault or run Git commands that change its
  files or repository state. The vault owner manages synchronization.
- Keep credentials, personal note contents, private reference documents,
  runtime state, and logs out of commits and agent review handoffs.
- Keep personal domains, hostnames, endpoint URLs, and deployment identifiers
  (including provider team names) out of Git-tracked files, including
  documentation and examples. Use reserved example domains or clearly marked
  placeholders.
- Test with small collections of invented notes. Automated tests must run
  without the real vault, external accounts, network services, or a local
  `.env`.
- HTTP tests use invented signing keys and an injected key fetch. The
  HTTP launcher's synthetic mode accepts only an exact copy of
  `tests/fixtures/vault/`, listed with SHA-256 digests in
  `src/knowledge_server/adapter/synthetic-vault.json`. When fixture notes
  change, update the manifest in the same change; the guard tests in
  `tests/test_http.py` fail until it matches. The guard also rejects more than
  100 entries or a file larger than 1 MiB.
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
4. **Validation:** Run the checks below and inspect the diff, including for
   additions that the requirements do not need.
5. **Drift prevention:** Update the specification, this guide, and usage
   and deployment instructions when a change makes them inaccurate. When a
   change adds a component or changes what one does, update the
   [architecture overview](docs/architecture.md). Include those updates in the
   same change as the code and tests.

An authorized task does not require separate approval for each workflow
step.

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
- Send diagnostic messages to stderr. Stdout carries MCP messages, so other
  output can break communication. Never log secrets, note contents, or queries.

## Validation

After code or configuration changes, run `scripts/check`. It installs the
locked dependencies, runs the formatting, lint, type, and test checks, checks
for whitespace errors, and summarizes the results. CI runs
`scripts/check --ci` automatically on pushes to `main` and, on request, on
another pushed branch: start it from the Actions tab or with
`gh workflow run check.yml --ref <branch>`. Validate unpushed work locally. For
any documentation change, check whitespace, links, claims, and examples,
and complete the documentation review below.
Repeat checks only after further changes or when investigating a failure.

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
  Write for a reader without the author's context: introduce a term or concept
  before relying on it. Keep precise technical terms, but spare the reader
  needless linguistic effort.
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
  [GLOSSARY.md](GLOSSARY.md). Explain unfamiliar concepts, and avoid unnecessary
  coined labels and misleading analogies, such as calling a task list "task
  cards."
- Avoid filler and canned conclusions such as "leverage," "it's worth noting,"
  "Bottom Line," and "In short."
- Define planned work as tasks in `docs/roadmap.md`. When a task is complete,
  remove its entry.
- Put each topic in the document whose role it serves. The design decisions
  record decisions, their reasons, and the risks the design accepts; the
  architecture overview describes the components; the contracts specify
  behavior; the guides give procedures and operational caveats. Do not merely
  describe how something works or how to set it up in the design decisions.
- Record a limitation that validation leaves open only where it helps a
  reader: in the design decisions when it explains a decision or an accepted
  risk, and in the relevant guide when it is an operational caveat. Leave
  out dates, observation logs, and validation status that support no
  decision; Git records them.
- Before drafting, identify the reader's need and choose one primary document
  for each topic. Match the detail to that document's role; summarize and link
  elsewhere. Draft from the final outcome, not the conversation or work log.
- Before finishing, review the documentation diff for relevance, placement,
  and duplication. Remove detail that does not help the reader understand,
  use, or maintain the project, including obsolete plans and incidental trial
  history. Preserve active requirements, open decisions, operational caveats,
  and evidence needed to reproduce or assess a result. Delete removed text
  instead of moving it to an appendix or archive. Reread the changed text as a
  new reader: check that each term is introduced, each sentence follows
  logically, and parallel items match.
- Wrap prose at about 80 columns. Tables, code, and long URLs are exempt.

## Docstrings and comments

Apply the documentation guidance above to docstrings and comments, and write
them so that the code stays readable and maintainable. Use Google-style
docstrings; Ruff checks their format.

- Public modules, classes, functions, and methods: after the summary line,
  describe the behavior a caller needs to know, unless the summary line says
  enough. Describe a class's fields in an `Attributes:` section. Describe
  request and result model fields with `Field(description=...)`, because the
  MCP tool schemas pass these descriptions to callers.
- Private helpers may omit the docstring when the code explains itself. Add a
  summary line, and more detail, whenever it helps a reader understand or
  maintain the code.
- Describe the code as it is. Do not refer to tasks, plans, or history, which
  change or disappear and leave the text stale.
- Comments explain reasons and constraints. When code does something that is
  not obvious from reading it, a comment may also explain what it does, but
  first consider whether clearer code would remove the need.

## Commits

Commit and push only when the user has authorized those actions for the
relevant scope. Use Conventional Commits for commit messages:
`type: description` or `type(scope): description`. Omit the body for
straightforward changes; add one when the reason or consequences need
explanation beyond the subject. Do not add co-author trailers or other agent
signatures to commit messages or pull request descriptions.
