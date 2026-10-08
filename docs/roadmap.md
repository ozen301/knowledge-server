# Roadmap

Status: no task is in progress. This document lists optional later work,
the constraints on it, and how to define and finish a task. The [design
decisions](design-decisions.md) record the decisions that later work must
preserve.

## How to add work

Start an item only when its trigger occurs, backed by evidence such as a failed
real question or a result of the [retrieval
questions](sample-notes.md#check-a-retrieval-change). Do not build frameworks
or backend abstractions in advance.

1. Define the work as a task under [Active tasks](#active-tasks): its goal,
   the specification changes, and the acceptance checks.
2. Implement and review one task at a time, as one reviewable diff, with the
   workflow in [AGENTS.md](../AGENTS.md).
3. When the task is complete, remove its entry, and update the documents
   that the change makes inaccurate, as the workflow in
   [AGENTS.md](../AGENTS.md) requires. Git keeps the history.

Revise the design decisions when evidence changes a decision.

## Active tasks

None.

## Candidates

| Idea | Trigger and constraint |
|---|---|
| Queries that require all of several words (AND), a rebuildable index such as SQLite FTS5, or ranking | Real questions fail because one literal phrase cannot combine separate words, or latency or search budgets block use; stale and missing sources must be handled |
| Additional formats, likely text-based PDF first | Needed sources exist outside Markdown; hits must remain traceable to the original file and page or section |
| Semantic retrieval with multilingual embeddings | The saved evaluation shows misses that lexical search cannot fix; exact search and CPU-only operation must remain useful |
| Additional collections | A second explicitly configured root is needed; source identity and filtering must stay consistent across tools and caches |

SQLite FTS5, document converters, and vector stores are candidates, not
current dependencies. Choose them only after the repeatable evaluation
demonstrates a need.

## Retrieval constraints

Preserve `knowledge_read` for line-oriented Markdown. If document conversion
introduces page or section citations, add an appropriate document identifier
and reader instead of forcing those formats into today's line model. Likewise,
add explicit ranked or hybrid search modes rather than changing literal mode.

## Other follow-ups

- **Orchestration efficiency.** After the next two or three substantial
  orchestrated tasks, or when the same friction recurs, review the runs for
  avoidable cost: retries, duplicate inspection or validation, weak handoffs,
  and blocked commands. Change the [orchestration
  skill](../.agents/skills/orchestrated-implementation/SKILL.md) only when
  the evidence supports a concrete improvement, following its rule for
  changes, and record sanitized conclusions only. The review delays a task
  only when it finds a correctness, security, or privacy risk.
