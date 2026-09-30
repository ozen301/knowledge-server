# Cross-family agents

This file is the only place in the skill that names model identifiers. They
were checked on 2026-09-30. If a tool rejects one, tell the user and choose
again.

| Role | Claude family | OpenAI (Codex) family |
|---|---|---|
| Investigator | `claude-haiku-4-5-20251001`; `claude-sonnet-5-5` when judgment is needed | `gpt-6-luna` |
| Writer | `claude-sonnet-5-5` | `gpt-6.1-sol`; `gpt-6-luna` for mechanical edits |
| Reviewer | `claude-opus-5-5` | `gpt-6.1-sol`; `gpt-6-astra` when the stakes justify its cost; `gpt-6-luna` for a narrow check |

Every command below has a deadline. Exit status 124 or 137 means the deadline
expired, and any other nonzero status means the run failed. Report both as
failures, never as "no findings."

## From Claude Code to Codex

```sh
timeout --kill-after=10 900 codex exec -m <id> \
  -c model_reasoning_effort=<level> -s read-only \
  --json -o ANSWER_FILE - < PROMPT_FILE > EVENTS_FILE
```

- An empty `ANSWER_FILE` is a failure. The thread ID is the `thread_id` of
  the first event in `EVENTS_FILE`. The events do not name the model; the
  `turn_context` entries of the session file
  `~/.codex/sessions/<date>/rollout-*-<thread-id>.jsonl` record the model
  and effort that Codex used for each turn.
- Follow-up (`resume` has no `-s` option, so set the sandbox with `-c`):

  ```sh
  timeout --kill-after=10 900 codex exec resume -m <id> \
    -c model_reasoning_effort=<level> -c 'sandbox_mode="read-only"' \
    -o ANSWER_FILE <thread-id> - < PROMPT_FILE
  ```

- The read-only sandbox has no writable temporary directory, so Codex cannot
  run pytest. The coordinating agent runs tests.
- A Codex writer uses `-s workspace-write` instead of `-s read-only`, run
  from the repository root. In testing, it could edit the repository and
  write under `/tmp`; `git commit`, writes elsewhere in the home directory,
  and network access failed. uv cannot write its cache in this sandbox, so
  the coordinating agent prepares `.venv` and the writer runs tests with
  `.venv/bin/python -m pytest`.
- The user-level `ask-codex` skill, if installed, runs the same read-only
  review with these checks. Its `--resume` continues only the newest Codex
  task of the current Claude session.

## From Codex to Claude Code

Run `claude` through a command-scoped escalation (`require_escalated`),
because it needs network access and saves its session under `~/.claude`.
Claude's own permission rules are then the only limit, so do not use
`bypassPermissions`.

Reviewer or investigator (read-only):

```sh
timeout --kill-after=10 900 claude --print --model <id> --effort <level> \
  --safe-mode --restricted --strict-mcp-config \
  --permission-mode dontAsk --permission-prompts none \
  --tools 'Read,Glob,Grep' --add-dir <dir-with-diff-file> \
  --output-format json < PROMPT_FILE > RESULT_FILE
```

Writer:

```sh
timeout --kill-after=10 1800 claude --print --model <id> --effort <level> \
  --safe-mode --restricted --strict-mcp-config \
  --permission-mode dontAsk --permission-prompts none \
  --tools 'Read,Glob,Grep,Edit,Write,Bash' \
  --allowedTools 'Edit Write Bash(uv run pytest *)' \
  --output-format json < ASSIGNMENT_FILE > RESULT_FILE
```

- `--restricted` confines file tools to the working directory and `--add-dir`
  directories. `--safe-mode` turns off project instructions, so name the
  governing files in the prompt.
- Anything not in `--allowedTools` is denied without a prompt. In testing, the
  writer was denied `git commit`, `curl`, `rm`, and writes outside the
  repository. Add commands only when the assignment needs them.
- The run succeeded only when `RESULT_FILE` has `is_error` set to `false`,
  `subtype` set to `"success"`, and a nonblank `result`. Record `session_id`,
  the keys of `modelUsage` (the models that ran), and `permission_denials`.
- Follow-up: run the same profile with `--resume <session_id>`.
