# Tool contract

Status: agreed implementation contract. This document specifies the three
read tools, the three [proposal tools](#write-proposals), their limits, and
their errors. Changes should update this document and the corresponding tests
together. The protected [HTTP entry point](http-contract.md) serves the read
tools with the same behavior and, when its configuration enables them, the
proposal tools. Stdio serves only the read tools.

## Configuration and common policy

- The root is the directory the tools expose: the local vault checkout or
  one subtree of it. For stdio, `KNOWLEDGE_ROOT` is required and names it;
  for HTTP, the [launch configuration](http-contract.md#launch-configuration)
  does. The root must be an explicit absolute path to an existing directory
  that is readable and searchable. It is resolved once on startup, and a root
  symlink may resolve at that point. Never default to the process working
  directory, home directory, or the vault remote. The read tools read files
  directly and do not inspect Git metadata, except `knowledge_read` with
  `proposal`. Only the [proposal tools](#write-proposals) and that read run
  Git, in a repository of the server's own.
- Root visibility is all non-hidden Markdown notes. Tool arguments cannot
  expand access beyond the configured root and common policy.
- API paths use `/`, relative to the configured root. Empty string means the
  root for list/search only. Accept spaces and Unicode. Preserve case and
  Unicode spelling; do not URL-decode paths.
- Reject absolute paths, Windows drive/UNC paths, backslashes, NUL/control
  characters in Unicode category `Cc`, and `.` or `..` path components. Reject
  repeated or empty internal components and multiple trailing slashes. A single
  trailing slash is accepted only for directory-capable inputs and requires the
  final target to be a directory; a trailing slash on a file-only input is
  invalid. Empty path is accepted only for directory-capable inputs. Do not use
  string-prefix tests for containment; inspect components with `lstat` before
  following anything and check resolved ancestry.
- Reject every symlink below the configured root, including links targeting
  another in-root file. Reject special files such as FIFOs and devices. Only
  regular files and real, non-symlink directories are eligible.
- Reject any hidden component (starting with `.`), including `.git` and
  `.obsidian`. Files must have a case-insensitive `.md` suffix. Do not expose
  other file names through listing.
- `.gitignore`, `.ignore`, global Git ignores, and ripgrep user configuration
  must not silently change visibility. An eligible ignored Markdown note
  remains accessible through every tool.
- A listing may show visible ordinary subdirectories even when they contain no
  eligible notes. It must not recurse into them automatically.
- File contents: strict UTF-8, no NUL bytes. A leading UTF-8 byte order mark
  (BOM) is accepted and is not part of the text, so a file that contains only
  a BOM is empty. A U+FEFF character elsewhere is ordinary text.
- Normalize CRLF to LF. After that, LF is the only line boundary: a lone CR
  and Unicode separators such as U+2028, U+2029, and U+0085 are characters
  within a line. This keeps line numbers consistent with ripgrep. A final
  newline does not create an extra empty line: `a\nb` and `a\nb\n` both have
  two lines, `\n` has one empty line, and an empty file has zero lines.
- Enforce the readable/searchable file-size limit below on the raw bytes,
  before BOM removal and CRLF normalization. Read at most that amount plus one
  sentinel byte rather than trusting a prior stat alone. Larger files may be
  listed but are not read or searched.
- Tool results and tool errors contain no host absolute paths, command lines,
  or raw subprocess stderr. This applies to server-generated metadata; do not
  rewrite path-like text authored inside a note.
- Logs go to stderr and exclude secrets, note contents, and query text,
  including values embedded in exception messages. Stdout carries only protocol
  output.

The shared policy determines path visibility; content/size checks determine
readability. A listing can show a visible file that is too large to read,
but hidden or disallowed paths are never exposed.

Validate a relative API path's lexical form before checking hidden components
or filesystem existence. The [error mappings](#error-and-change-behavior)
define the resulting codes and the remaining order of checks.

## Initial limits

These values are enforced. Tests may inject smaller limits to exercise
boundaries without large or slow fixtures.

| Setting | Initial value |
|---|---|
| Maximum readable/searchable file size | 1 MiB |
| Maximum queries per search | 5 |
| Maximum query length | 512 Unicode code points, for each query |
| Search results: default / maximum | 20 / 50 |
| Maximum snippet length | 300 Unicode code points |
| Read range: default / maximum | 200 / 200 lines |
| Maximum returned read content | 32 KiB of UTF-8 line text, without line-number prefixes |
| Directory page: default / maximum | 100 / 200 entries |
| Search deadline for the full operation | 10 seconds |
| Maximum filesystem entries visited per search | 10,000 |
| Maximum source bytes per search | 16 MiB |
| Maximum subprocess output per search | 4 MiB |
| Maximum immediate entries scanned per directory listing | 10,000 |
| Maximum text per write request | 256 KiB of UTF-8 |
| Maximum edits per edit request | 100 |
| Maximum branches and changed paths scanned per proposal listing | 10,000 |
| Maximum output per Git command | 4 MiB |
| Git deadline: local command / fetch or push | 10 / 30 seconds |

The filesystem-entry budgets count every directory entry inspected before
visibility filtering, but excluded directories are not traversed. The
source-byte budget counts raw bytes actually loaded while validating candidate
files, including sentinel bytes and bytes from files later skipped, even when
a later read of the file fails. The subprocess-output budget and the Git
output limit count stdout and stderr together. The listing limit counts
branches and changed paths before filtering.

The write-text limit counts the UTF-8 bytes of `content`, or of all `old` and
`new` texts of an edit request together. JSON escaping can enlarge the text in
the request. Ordinary Markdown grows little, and non-ASCII characters at most
triple when written as `\uXXXX`. A text at the limit can exceed the HTTP entry
point's 1 MiB request body only if the client writes most characters as
`\uXXXX`; the entry point then returns 413, and nothing is written. The edit
limit bounds the work of one request, because each edit searches the whole
note.

## Tools

All numbers are strict integers (booleans and numeric strings are invalid).
Reject invalid bounds rather than silently widening requests. Return
object-shaped typed results. Use the SDK's typed-output support to produce
successful structured content and its equivalent JSON text representation. Map
domain failures to MCP tool errors with `isError=true`; let the SDK handle
malformed protocol requests. Arguments that do not match a tool's input schema
or its bounds, including unknown, missing, or wrongly typed fields, are a
domain failure with `INVALID_ARGUMENT`, not an SDK validation message. Every
read tool is annotated as read-only, non-destructive, idempotent, and
closed-world; [write proposals](#write-proposals) gives the proposal tools'
annotations. Annotations describe the tools but do not enforce the policy.
[Official tool
specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools),
[SDK structured
output](https://py.sdk.modelcontextprotocol.io/servers/structured-output/).

### knowledge_search

```text
knowledge_search(
    queries: list[str],
    path: str = "",
    max_results: int = 20,
    case_sensitive: bool = false
)
```

- `path` may name an eligible file or visible directory; directory search is
  recursive.
- `queries` holds 1 to the query-count maximum queries. A line matches when
  it contains at least one of them (OR), so one call can try alternative
  wordings. Duplicate queries are allowed and have no effect.
- Each query is a literal substring within one line. Preserve spaces; reject
  whitespace-only queries, newlines, NULs, and queries exceeding the
  query-length limit. No regex, query language, stemming, or implicit word
  splitting.
- Search compares the notes and the queries in Unicode Normalization Form C
  (NFC), so canonically equivalent text matches: `é` stored as one code point
  or as `e` followed by a combining accent (U+0301), and `が` stored as one
  code point or as `か` followed by a combining voiced mark (U+3099). The
  query limits apply to the queries as sent, before normalization. A query
  matches whole NFC characters only, so `Herve` does not match `Hervé` in
  either form. Compatibility forms remain distinct, because search does not
  apply NFKC: full-width `＋` does not match half-width `+`.
- Case behavior follows ripgrep's Unicode-aware case-insensitive matching on
  the NFC text unless `case_sensitive=true`; include non-ASCII cases in
  fixtures. A few equivalent forms do not match ignoring case, because NFC
  composes one case but not the other: `j` followed by U+030C becomes `ǰ`
  (U+01F0), while `J` followed by U+030C has no composed form.
- One hit per matching line, even with multiple occurrences on that line or
  matches of several queries. A hit does not say which query matched. Sort
  paths case-sensitively by Unicode code-point order, then by line number. No
  relevance score.
- `max_results` must be at least 1 and at most the search-result maximum. It
  limits the hits of all queries together, so a query with many hits in
  early paths can leave no room for the others. Fetch one additional hit to
  determine result-limit truncation.
- Snippet: a window of the NFC form of the matching line around the first
  match of any query on that line, as [defined below](#snippet-window). Paths
  and line numbers refer to the original note and allow a full read.
- Search does not paginate: narrow the path or the queries when truncated.
- Missing/disallowed explicit targets are errors. During recursive search,
  unreadable, invalid-encoding, oversized, or concurrently removed files are
  skipped and counted by reason, without exposing hidden filenames.

Result shape:

```json
{
  "matches": [
    {"path": "Hardware/NAS.md", "line": 12, "snippet": "ECC memory", "snippet_truncated": false}
  ],
  "truncated": false,
  "skipped": {"too_large": 0, "invalid_text": 0, "unreadable": 0}
}
```

`truncated` means more matches were found than returned. `skipped` counts,
by reason, the eligible files that could not be searched; a nonzero count
means that the result may miss matches, independently of `truncated`.
Ordinary policy exclusions do not count as skipped. Do not claim an exact
total hit count.

#### Snippet window

The snippet is an exact substring of the NFC form of the matching line, with
no ellipsis or other marker added. The line is the reader's version of it:
without a leading BOM or the line ending, and without whitespace trimming.
Where the note stores decomposed text, the snippet looks the same as the note
but contains different code points. Build the `old` text of an edit from
`knowledge_read`, not from a snippet: a snippet's text can fail to match the
note, or match a different line. The first match is the first occurrence of
any query that ripgrep reports on the line. Its start and end are code point
positions derived from ripgrep's reported byte offsets, not from the query
length, because a case-insensitive match can differ in length from the
query. With `L` as the snippet-length limit, a line of at most `L` code points
is the whole snippet. For a longer line, the snippet is exactly `L` code
points; a match that fits is centered, with an odd extra code point after it,
and the window slides to stay inside the line.
With `start` and `length` measured in code points:

```text
if length <= L: start = clamp(match_start - (L - length) // 2, 0, len(line) - L)
else:           start = match_start
snippet = line[start : start + L]
```

`snippet_truncated` is true when the snippet is shorter than the whole line.
It does not show whether the match itself was cut; that happens only for a
match longer than `L`, and the caller can read the whole line. Because the
window counts code points, a cut can split a character made of several code
points, such as a letter with a combining accent or an emoji sequence.

For example, with `L = 10`, the line `abcdefghijklmnop` and the query `h`
give `defghijklm`; the query `b` gives `abcdefghij`; the query `o` gives
`ghijklmnop`.

Count a discovered file that disappears or becomes inaccessible before it can
be read as `unreadable`. A subdirectory that cannot be scanned also counts as
one `unreadable` item, because the notes in it could not be searched. An
explicit target follows the [error mappings](#error-and-change-behavior)
instead. If a directory is replaced by a symlink after it was checked, its
entries are rejected when they are checked from the root, so its notes are
missing from the result without being counted; no name from outside the root
is exposed.

Search loads each candidate note with the reader's bounded loader, which
opens every path component without following symlinks, and counts skipped
notes by reason. It then sends the loaded text, in result order, to one
ripgrep process on standard input. The text is the reader's version,
without a BOM, with CRLF normalized to LF, and with every line ending in LF,
converted to NFC. NFC does not add or remove line breaks, so the line numbers
are those of the original note. ripgrep never opens a vault file, so a file
changed or replaced after loading cannot affect the matches. Its
`--max-count` of `max_results + 1` stops the search once the result is known
to be truncated. ripgrep requirements:

- Fixed-string JSON output, `--encoding none` so that byte offsets refer to
  the bytes sent, and an explicit case option.
- An argument-list call (`shell=False`) with each query passed as the value
  of its own `-e`, explicit options, and a minimal environment, so user
  configuration cannot change behavior.
- Each reported line must equal the sent line at the reported position;
  otherwise the result is `SEARCH_FAILED`.
- Standard input is written while standard output and error are read,
  concurrently and in bounded chunks, and the output budget is checked before
  more output is kept. ripgrep may stop reading its input
  early after `--max-count`; that is not a failure.
- Exit status 0 means matches, 1 means no matches, and any other status or a
  signal is `SEARCH_FAILED`.
- On timeout, cancellation, or an exceeded budget, the process is killed and
  reaped. A further cancellation during the reap takes effect after it.

The deadline starts with the request. Discovery and loading run outside the
event loop and stop at the next entry, file, or read chunk after the deadline
or a cancellation. A filesystem call that is already blocked cannot be
interrupted: the search still returns at the deadline, and the background
work stops when that call returns.

Exceeding the deadline or the visited-entry, source-byte, or subprocess-output
budget is a `SEARCH_LIMIT_EXCEEDED` error, without partial results. Only the
caller's `max_results` limit produces a successful truncated response.
ripgrep's JSON output contains each matching line in full, so a few matches on
very long lines can exceed the output budget.

### knowledge_read

```text
knowledge_read(path: str, start_line: int = 1, end_line: int | null = null)
```

When the HTTP entry point serves the [proposal tools](#write-proposals),
`knowledge_read` also takes `proposal: str | null = null`, the name of a
proposal branch. The tool then fetches the branch from the vault remote and
reads the note from its tip, with the same results, limits, and errors as a
read from the root and the path policy of proposals. Without `proposal`, it
reads from the root.

- Line numbers are one-based and inclusive. `end_line=null` requests the
  default read range from `start_line`. Explicit ranges must fit the maximum
  read range.
- Negative/zero line numbers, reversed ranges, and oversized requested ranges
  are invalid. Clamp the actual end to EOF.
- Starting after EOF succeeds with empty `numbered_content` and null actual
  line bounds.
  Return `total_lines` so the caller can recover.
- Return whole lines in `numbered_content`. Each line is its line number in
  decimal without padding, a tab, the line text, and LF, for example
  `7\t- **CPU:** AMD Ryzen 5 2600X\n`. Blank lines are included. Every
  returned line ends with LF, including a last line that has no final newline
  in the file, so consecutive pages join by concatenation. Removing the number
  and tab from each line gives the note text. When no lines are returned,
  `numbered_content` is empty. The numbers let a caller cite a line without
  counting lines.
- The returned-content limit counts the UTF-8 bytes of the line text, without
  the number prefixes: after BOM removal and CRLF normalization, and including
  each line's LF. The prefixes do not change where a read stops. If the next
  line would exceed the limit, stop before it and provide continuation. If the
  first requested line alone exceeds the limit, return `LINE_TOO_LONG` rather
  than silently slicing it. With the 32 KiB limit, a line of 32,767 bytes plus
  its LF fits; a line of 32,768 bytes does not.
- `next_line` points to the next unread line whenever the file has more lines,
  even when the requested range was fully returned. It is null at EOF.
  `truncated` is true only when the byte limit prevented fulfilling the
  requested range; selecting a small range is not itself truncation. Clamp the
  requested end to EOF before deciding truncation.
- Include SHA-256 of the loaded raw bytes as `content_sha256`. This identifies
  the bytes used for this response, including uncommitted edits; it does not
  guarantee a filesystem snapshot.

Result fields: `path`, `numbered_content`, `start_line` and `end_line` (actual
bounds or null), `total_lines`, `next_line`, `truncated`, `content_sha256`.

Examples for a file of ten short lines, unless stated otherwise:

| Request | `start_line`, `end_line` | `next_line` | `truncated` |
|---|---|---|---|
| lines 1-5 | 1, 5 | 6 | false |
| lines 6-10, or `start_line=6` with the default range | 6, 10 | null | false |
| lines 8-20 | 8, 10 | null | false |
| `start_line=11` | null, null (`numbered_content` is empty) | null | false |
| lines 1-10, where the byte limit is reached after line 4 | 1, 4 | 5 | true |
| `start_line=5` in that file, where line 5 alone exceeds the limit | `LINE_TOO_LONG` error | | |

`total_lines` is 10 in each successful case.

### knowledge_list

```text
knowledge_list(path: str = "", offset: int = 0, limit: int = 100)
```

- Immediate children only, ordered case-sensitively by root-relative path in
  Unicode code-point order. Return root-relative paths and `kind` (`file` or
  `directory`). Apply visibility rules before pagination.
- `offset >= 0`; `limit` must be at least 1 and at most the directory-page
  maximum. Offset beyond the list gives an empty result. This is best-effort
  pagination of the live directory; edits between calls may shift entries.
- Result fields: `path`, `entries`, and `next_offset`, which is null on the
  last page.
- Enforce the immediate-entry scan limit; beyond that return
  `DIRECTORY_LIMIT_EXCEEDED`. Do not build an unbounded directory listing.
- Known limitation: if the listed directory is replaced by a symlink between
  the policy check and the scan, the listing can return an empty page instead
  of `ACCESS_DENIED`. Each entry is still checked from the root, so no names
  from outside the root are exposed.

### Write proposals

When its [configuration](http-contract.md#launch-configuration) sets
`vault_remote`, the vault remote's URL or path, the HTTP entry point serves
three proposal tools: `knowledge_propose_note` and `knowledge_propose_edit` save
proposals, and `knowledge_list_proposals` lists them. A proposal is a branch of
the vault remote, named `proposal/<topic>-<suffix>`, that holds unreviewed
changes to one or more notes. It takes effect only when the vault owner merges
it into `main`. The server never changes the local vault checkout: it creates
the commits in a bare repository of its own and pushes them to the vault remote.

- **New proposals.** A write without `proposal` needs `topic`, a short name
  for the change such as `sleep-notes-cleanup`: 1 to 40 lowercase letters
  and digits, in groups joined by single hyphens. The tool starts a branch
  from the commit that the local vault checkout has checked out (`HEAD`) and
  names it `proposal/<topic>-<suffix>`, where the suffix is 4 random
  lowercase hexadecimal digits. Uncommitted changes in the checkout are not
  part of that commit.
- **Revisions.** A write with `proposal`, the name of an existing proposal
  branch, fetches the branch from the vault remote and adds a commit on its
  tip. A missing branch returns `NOT_FOUND`. A request with both or neither
  of `topic` and `proposal`, or with a `proposal` that does not match the
  branch name pattern, returns `INVALID_ARGUMENT` before any Git command
  runs. Below, the *selected commit* is the checkout's `HEAD` for a new
  proposal and the fetched branch tip for a revision.
- **Summary.** Both write tools require `summary`, one line that explains
  the change to the reviewer: 1 to 200 Unicode code points, not only
  whitespace, and without control characters (category `Cc`) or the line
  and paragraph separators U+2028 and U+2029. Any other value returns
  `INVALID_ARGUMENT`. The commit message is `Unreviewed proposal: <summary>`.
- **Path policy.** Tool paths are relative to the root. When the root is a
  subtree of the checkout, commit paths also contain the subtree's path. A
  path argument follows the common policy for a file: the lexical rules,
  hidden components, and the `.md` suffix, with the same order of checks and
  the same error codes. In the selected commit, an entry on the path that is
  a symlink or anything other than a regular file or directory returns
  `ACCESS_DENIED`, and a file where a directory is needed returns
  `NOT_A_DIRECTORY`.
- **Text.** No text argument may contain NUL, CR, or U+FEFF; LF is the only
  line break, and a new note can never start with BOM bytes. Text beyond the
  [write-text limit](#initial-limits) or containing these characters returns
  `INVALID_ARGUMENT`.
- **Size.** The bytes written, including a BOM, must fit the readable file
  size limit, so that every proposal stays within the limit of the read
  tools. A larger result returns `FILE_TOO_LARGE`.
- **Writes.** One lock serializes all proposal operations of the process, from
  loading to the push, so a check and the write that depends on it cannot
  interleave with another write. A push never overwrites a branch: a new branch
  is pushed only if no branch of that name exists, and a revision only if the
  branch tip is still the one that it started from. Otherwise the result is
  `ALREADY_EXISTS` for a new branch and `STALE_CONTENT` for a revision. Any
  other failure of Git or the vault remote, including the [Git
  limits](#initial-limits), returns `GIT_FAILED`. A tool returns its result only
  after the push succeeds. If the connection fails during a push, the remote may
  still have accepted it; list the proposals before a retry.
- **Results.** Both write tools return `proposal`, the branch name; `path`,
  the root-relative note path; and `content_sha256`, the SHA-256 of the
  bytes written. That is the hash `knowledge_read` reports for the note on
  that branch, so a following edit with the same `proposal` can pass it as
  `base_sha256` without reading again.
- **Annotations.** Both write tools are annotated as not read-only, not
  idempotent, and closed-world. `knowledge_propose_note` is non-destructive,
  because it only adds a note. `knowledge_propose_edit` is destructive,
  because it replaces text. `knowledge_list_proposals` has the read tools'
  annotations.
- **Descriptions.** Both write descriptions say that the tool saves a
  proposal on an unreviewed branch, which the user reviews and merges, and
  when to pass `topic` or `proposal`. The description of
  `knowledge_propose_edit` also says to include surrounding lines when a
  text to replace is not unique. Both descriptions say that the note
  conventions for the vault are in `AGENTS.md` at the root, if it exists,
  and that note text links to another note by its vault path. They say what
  the tools do and what their arguments need, not when a client may call
  them. The description of `knowledge_read` explains `proposal`.

#### knowledge_propose_note

```text
knowledge_propose_note(summary: str, path: str, content: str,
                       topic: str | null = null, proposal: str | null = null)
```

- `path` is the vault path that the new note should have, such as
  `Projects/Plan.md`. An entry at `path` in the selected commit returns
  `ALREADY_EXISTS`. Its parent directories need not exist, but those that
  exist must be directories.
- `content` is written as UTF-8 with LF line endings and no BOM. It may be
  empty.

Checks run in this order: arguments, the path policy except existence, the
selected commit, the entries on the path, the result size, and the push.

#### knowledge_propose_edit

```text
knowledge_propose_edit(summary: str, path: str, base_sha256: str,
                       edits: list[Edit],
                       topic: str | null = null, proposal: str | null = null)
Edit = {old: str, new: str}
```

- `path` names a note in the selected commit.
- `base_sha256` is 64 lowercase hexadecimal characters. It must equal the
  SHA-256 of the note's raw bytes in the selected commit, for example the
  `content_sha256` of an earlier read; otherwise the result is
  `STALE_CONTENT`. The tool loads the note with the size and encoding checks
  of a read, so an oversized or invalid note returns `FILE_TOO_LARGE` or
  `INVALID_ENCODING`.
- `edits` holds 1 to the [edit limit](#initial-limits) of edits. Each `old`
  is non-empty; `new` may be empty, which deletes `old`.
- The edits apply in order to the note's decoded text, as the read tools see
  it: without a BOM and with CRLF normalized to LF. A note without a final
  newline has none in this text, although `knowledge_read` ends every
  returned line with LF; an edit can add one. Each `old` must occur
  exactly once, counting overlapping occurrences, in the text that the
  earlier edits produced. No occurrence returns `TEXT_NOT_FOUND`, and more
  than one returns `TEXT_NOT_UNIQUE`; both messages give the edit's position
  in `edits`, counted from 1. If any edit fails, nothing is written.
- The proposal keeps the note's encoding details and file mode. It starts
  with a BOM if the loaded bytes do. If the loaded bytes contain CRLF, every
  LF of the result is written as CRLF; otherwise LF stays. Bytes that contain
  both CRLF and an LF without a preceding CR return `MIXED_LINE_ENDINGS`,
  because either choice would change lines that the edits did not touch.

Checks run in this order: arguments, the path policy except existence, the
selected commit, the entries on the path and the load, `STALE_CONTENT`,
`MIXED_LINE_ENDINGS`, the edits, the result size, and the push.

#### knowledge_list_proposals

```text
knowledge_list_proposals()
```

The tool fetches `main` and all proposal branches from the vault remote and
returns `proposals`, ordered by branch name. Each entry has `proposal`, the
branch name, and `paths`, the notes that the branch changes, relative to the
root and sorted. These are the notes that differ between the branch tip and
its merge base with `main`, so the list of a merged branch is empty until
the vault owner deletes the branch. Paths outside the root and entries that
the path policy excludes, such as hidden paths, symlinks, and non-Markdown
files, are left out, and so are branches whose names do not match the
pattern. A listing that scans more branches and changed paths than the
[listing limit](#initial-limits) returns `DIRECTORY_LIMIT_EXCEEDED`.

## Error and change behavior

Domain errors have `code` and a short safe `message`. A tool returns a domain
error as a result with `isError=true` and one text content item that holds
the JSON object `{"code": "NOT_FOUND", "message": "..."}`; the error result
has no structured content. Each code has one fixed message, with two
exceptions. The message of `INVALID_ARGUMENT` names each rejected argument and
the values that argument accepts, for example `max_results must be an integer
from 1 to 50.` The accepted values come from the request models, so the
message follows the limits. An unknown argument is not named; the message
lists the valid arguments instead. The messages of `TEXT_NOT_FOUND` and
`TEXT_NOT_UNIQUE` give the position of the failing edit. No message repeats a
value from the request. Codes: `INVALID_ARGUMENT`, `INVALID_PATH`,
`NOT_FOUND`, `ACCESS_DENIED`, `UNSUPPORTED_TYPE`, `NOT_A_FILE`,
`NOT_A_DIRECTORY`, `FILE_TOO_LARGE`, `INVALID_ENCODING`, `LINE_TOO_LONG`,
`SEARCH_LIMIT_EXCEEDED`, `DIRECTORY_LIMIT_EXCEEDED`, `SEARCH_FAILED`,
`ALREADY_EXISTS`, `GIT_FAILED`, `STALE_CONTENT`, `TEXT_NOT_FOUND`,
`TEXT_NOT_UNIQUE`, `MIXED_LINE_ENDINGS`, `INTERNAL_ERROR`. The [write
tools](#write-proposals) define the codes that only they return.

Check path policy before reporting existence so hidden/disallowed paths do not
become existence probes. Apply these mappings consistently:

- Malformed paths return `INVALID_PATH`.
- Hidden components and symlinks return `ACCESS_DENIED`.
- When an operation requires a file, a final path component without a
  case-insensitive `.md` suffix returns `UNSUPPORTED_TYPE`, checked before
  existence. A request that accepts either a file or directory, such as search,
  accepts visible directories regardless of suffix and returns
  `UNSUPPORTED_TYPE` for an identified non-Markdown regular file.
- Special files such as FIFOs and devices return `ACCESS_DENIED`.
- Missing eligible targets return `NOT_FOUND`, including when a file
  disappears before opening.
- A regular file supplied where a directory is required returns
  `NOT_A_DIRECTORY`, including a non-Markdown file; a directory supplied where
  a file is required returns `NOT_A_FILE`.
- Permission failures return `ACCESS_DENIED`.
- Explicit oversized read/search targets return `FILE_TOO_LARGE`.
- Explicit invalid UTF-8 or NUL-containing read/search targets return
  `INVALID_ENCODING`.

Failure to configure the root or locate ripgrep is a startup error with a
nonzero exit status, not a live half-working server. Startup looks for the
`rg` executable on the server process's `PATH`.

An unexpected exception in any tool becomes a generic `INTERNAL_ERROR`, never
raw exception text, and its log entry follows the content restrictions above.
Cancellation still propagates to active work.

Each operation on the root reads the local vault checkout on a best-effort
basis.
Concurrent edits may affect a read, and search locations can become stale
before a subsequent call. Read derives its content, line count, and hash
from the bounded bytes it loads. There is no snapshot guarantee or
general concurrent-edit detection; only an edit proposal checks
`base_sha256`. Do not cache content between requests. Callers can repeat
search/read if a note changes.

## Required behavioral examples

Fixtures must exercise ordinary notes, empty files, CRLF/BOM, Unicode content
and filenames, spaces/leading dashes in names, duplicate line matches, lines
that match several queries, ignored Markdown, hidden files, traversal,
symlinks inside/outside the root, root-prefix collisions, wrong types, invalid
UTF-8/NULs, oversized files/lines, output truncation, and missing files.

Tests use a temporary synthetic vault and an outside sentinel file, and check
that no tool reveals hidden or symlinked content. Cover subprocess arguments,
exit codes, timeout cleanup, and bounded output as well as ordinary search
success.

Proposal tests use temporary local Git repositories with invented notes and need
no network. They cover a new note, an edit, a revision, refused pushes, a
missing branch, a failed push, an invalid summary, a subtree root, listing,
reads from a branch, the path policy for branch contents, text, edit, and result
limits, exact replacements, and BOM and CRLF preservation. They check that the
local vault checkout does not change.
