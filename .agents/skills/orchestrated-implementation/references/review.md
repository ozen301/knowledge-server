# Independent review

Run an independent review when the user asks for one. Send only material the
user expects to share with the reviewer's provider: no credentials, `.env`
contents, knowledge-vault notes, or private reference documents.

1. Save the change as a diff file in a scratch directory outside the
   repository: `git diff HEAD`, plus
   `git diff --no-index -- /dev/null <file>` for each untracked file that
   belongs to the change; this command exits with status 1 when it finds a
   difference. Do not edit the working tree until the initial review response
   and any step 6 proposal feedback are complete, so the reviewer sees the
   validated code.
2. In the prompt, give the commit, the diff file, and the governing files
   (`AGENTS.md`, the specification, the task entry), and say "read-only;
   change nothing." Require one line per finding: `defect|improvement | claim
   | violated requirement or concrete failure scenario | file:line`.
3. Run the reviewer with a command from [cross-family.md](cross-family.md),
   with a model and effort chosen for the review's scope and risk, unless the
   user chose them.
   Keep raw output outside the repository. A timeout, a nonzero exit status,
   or an empty or failed answer is a failed review; report it as such, never
   as "no findings."
4. Follow-up questions about the same diff may resume the reviewer's
   session. To check applied fixes, save a new diff as in step 1, point out
   the fixes in it, and resume that session: ask whether each fix implements
   its step 6 proposal and resolves its finding. Start a fresh session
   instead, with the new diff and your response to each finding, when a fix
   goes beyond its proposal or changes code unrelated to the reviewed
   findings.
5. Check each finding against the repository, and classify it as confirmed,
   rejected (with the reason), or not verified. A reviewer may propose a
   reproducer; it is not evidence until you have run it and seen it fail for
   the claimed reason.
6. During a review the user requested, when a finding leads you to propose a
   fix or a design change, send that exact proposal to the same reviewer
   session before you apply it or ask the user to approve it. Report the
   reviewer's verdict with your own verification. To review the fix after you
   apply it, follow step 4.
7. In the task report, record the requested model and effort, the model that
   ran (or "not verified"), the commit, the elapsed time, the thread or
   session ID, and whether the review completed.
