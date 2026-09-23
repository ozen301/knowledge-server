# External CLI review

Use an external model only when the user has authorized sending the selected
repository material to that provider. Keep credentials, private knowledge-vault
contents, personal reference documents, and unrelated changes out of the
review handoff.

## Prepare the review

1. Inspect the installed CLI help before constructing the command. Confirm the
   exact requested model identifier, effort option, non-interactive mode, tool
   restrictions, and authentication status. Do not silently substitute a model.
2. Ask for a read-only review of the governing instructions, task and spec,
   tracked diff, and only untracked files that the owner has individually named
   and inspected. Require findings ordered by severity, precise file and line
   references, an explicit distinction between blocking defects and optional
   improvements, and a statement when no actionable findings exist.
3. Allow only the inspection commands the reviewer needs. Disable repository
   writes, session persistence, nested delegation, staging, and commits.

## Check network access before a provider request

Before launching an external CLI, inspect the active network permissions. When
network access is known to be restricted, request the necessary command-scoped
network approval up front, using Codex `require_escalated` when it applies.
Keep the selected isolated snapshot and the CLI's read-only tools. Do not send
a request that is known to fail merely to rediscover the restriction, disable
the sandbox globally, or change persistent configuration.

When connectivity is unknown, distinguish local CLI initialization and
authentication status from a successful provider request. Inspect reported
`api_retry` events and stop a bounded retry loop to diagnose the condition
before trying again. This is conditional guidance; a review does not always
need escalation.

For Claude Code, first confirm the installed version supports the flags, then
adapt this non-interactive command shape:

Claude Opus 5.5 uses the exact model identifier `claude-opus-5-5`. Preserve an
explicit user selection and the historical identity of the model that performed
an earlier review. See the [Opus 5.5 overview](https://platform.claude.com/docs/en/models/opus-5-5/overview).

```sh
claude --print --model <exact-model> --effort <requested-effort> \
  --safe-mode --restricted --strict-mcp-config \
  --no-session-persistence --permission-mode dontAsk \
  --permission-prompts none --tools 'Read,Glob,Grep' \
  --output-format stream-json --verbose \
  '<read-only review prompt>' < /dev/null
```

This default exposes no shell or writing tool. If the reviewer must run Git
inspection commands, first verify the installed allowlist syntax, then add
`Bash` to `--tools` and allow only the exact read-only commands required. Never
grant general shell access for convenience.

`--print` is the long form of `-p`. Close stdin explicitly because some command
runners keep their input pipe open, while the CLI can also accept prompt input
from stdin. Streaming output makes startup, model identity, and progress
observable. If the process remains silent past a reasonable startup window,
stop that process and check syntax, stdin, authentication, and network access
before retrying.

## Evaluate the response

Record the CLI version, actual model metadata, effort setting, sanitized command
shape, and whether any fallback or delegated agents were used. The owner must
check each reported issue against the repository and may reject unsupported or
out-of-scope suggestions. External review supplements local tests and static
checks; it does not replace them.

When substantive fixes follow a reviewed snapshot and final independent
verification is requested, review the delta with the fix dispositions and
validation evidence. Report whether the final code received external re-review;
optional nits do not by themselves require another full review loop.
