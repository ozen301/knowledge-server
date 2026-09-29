# Independent review

Run an independent review when the user asks for one. Send only material the
user expects to share with the reviewer's provider: no credentials, `.env`
contents, knowledge-vault notes, or private reference documents.

1. Save the change as a diff file in a scratch directory outside the
   repository: `git diff HEAD`, plus
   `git diff --no-index -- /dev/null <file>` for each untracked file that
   belongs to the change; this command exits with status 1 when it finds a
   difference. Do not edit the working tree until the review ends, so the
   reviewer sees the validated code.
2. In the prompt, give the commit, the diff file, and the governing files
   (`AGENTS.md`, the specification, the task entry), and say "read-only;
   change nothing." Require one line per finding: `defect|improvement | claim
   | violated requirement or concrete failure scenario | file:line`.
3. Run the reviewer with a command from [cross-family.md](cross-family.md).
   Keep raw output outside the repository. A timeout, a nonzero exit status,
   or an empty or failed answer is a failed review; report it as such, never
   as "no findings."
4. Follow-up questions about the same diff may resume the reviewer's
   session. To review later fixes, save a new diff and start a fresh session
   with the new diff and your response to each finding.
5. Check each finding against the repository, and classify it as confirmed,
   rejected (with the reason), or not verified. A reviewer may propose a
   reproducer; it is not evidence until you have run it and seen it fail for
   the claimed reason.
6. In the task report, record the requested model and effort, the model that
   ran (or "not verified"), the commit, the elapsed time, the thread or
   session ID, and whether the review completed.
