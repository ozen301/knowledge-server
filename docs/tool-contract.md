# Tool contract

Status: agreed implementation contract. This document specifies the four
read tools, the two [write tools](#write-proposals), their limits, and their
errors. Changes should update this document and the corresponding tests
together. The protected [HTTP entry point](http-contract.md) serves the read
tools with the same behavior and, when its configuration enables them, the
write tools. Stdio serves only the read tools.

## Configuration and common policy

- The root is the directory the tools expose: the local vault checkout or
  one subtree of it. For stdio, `KNOWLEDGE_ROOT` is required and names it;
  for HTTP, the [launch configuration](http-contract.md#launch-configuration)
  does. The root must be an explicit absolute path to an existing directory
  that is readable and searchable. It is resolved once on startup, and a root
  symlink may resolve at that point. Never default to the process working
  directory, home directory, or the vault remote. The server reads files
  directly; it does not inspect Git metadata or run Git commands.
- Root visibility is all non-hidden Markdown notes, including the proposals
  in the [inbox](#write-proposals). Tool arguments cannot expand access
  beyond the configured root and common policy.
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
  listed and inspected but are not read or searched.
- Tool results and tool errors contain no host absolute paths, command lines,
  or raw subprocess stderr. This applies to server-generated metadata; do not
  rewrite path-like text authored inside a note.
- Logs go to stderr and exclude secrets, note contents, and query text,
  including values embedded in exception messages. Stdout carries only protocol
  output.

The shared policy determines path visibility; content/size checks determine
readability. Listing/info can reveal that a visible file is too large, but
hidden or disallowed paths are never exposed.

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

The filesystem-entry budgets count every directory entry inspected before
visibility filtering, but excluded directories are not traversed. The
source-byte budget counts raw bytes actually loaded while validating candidate
files, including sentinel bytes and bytes from files later skipped, even when
a later read of the file fails. The subprocess-output budget counts stdout and
stderr together.

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
closed-world; [write proposals](#write-proposals) gives the write tools'
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
    case_sensitive: bool = false,
    mode: Literal["literal"] = "literal"
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
  "mode": "literal",
  "matches": [
    {"path": "Hardware/NAS.md", "line": 12, "snippet": "ECC memory", "snippet_truncated": false}
  ],
  "truncated": false,
  "incomplete": false,
  "skipped": {"too_large": 0, "invalid_text": 0, "unreadable": 0}
}
```

`truncated` means more matches were found than returned. `incomplete` means
eligible content could not all be searched because files were skipped; it is
independent of `truncated`. Ordinary policy exclusions do not count as skipped.
Do not claim an exact total hit count.

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
changed or replaced after loading cannot affect the matches. Its `--max-count` of `max_results + 1` stops the search once the
result is known to be truncated. ripgrep requirements:

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
- Result fields: `path`, `entries`, `next_offset` (null at end), `truncated`
  (more entries exist).
- Enforce the immediate-entry scan limit; beyond that return
  `DIRECTORY_LIMIT_EXCEEDED`. Do not build an unbounded directory listing.
- Known limitation: if the listed directory is replaced by a symlink between
  the policy check and the scan, the listing can return an empty page instead
  of `ACCESS_DENIED`. Each entry is still checked from the root, so no names
  from outside the root are exposed.

### knowledge_info

```text
knowledge_info(path: str)
```

- Files only. Return `path`, `size_bytes`, `modified_at` (UTC RFC 3339),
  `line_count`, `content_sha256`, and `readable`.
- For a readable file, compute line count and hash from the same bounded byte
  load. For oversized/invalid-text files, return `readable=false`, null line
  count/hash, and `unreadable_reason` (`too_large` or `invalid_text`). For
  ordinary readable files, `unreadable_reason=null`.
- Permissions failures are errors. Modification time is filesystem metadata,
  not a guarantee that every writer preserves timestamps.

### Write proposals

The two write tools save text as proposals in the inbox, the directory
`inbox/` directly under the root. They never change a file outside the
inbox: a proposal takes effect only when the vault owner merges it into the
knowledge vault. Only the HTTP entry point serves them, and only when its
[configuration](http-contract.md#launch-configuration) enables them.

- **Inbox paths.** The proposal for the vault path `P` is the note
  `inbox/P`, and there is at most one. The read tools serve the inbox as an
  ordinary visible directory, so search and listing from the root include
  it. An edit of a vault note also keeps a base copy of the version it
  started from at `inbox/.base/P`. The hidden-component rule keeps base
  copies and temporary files out of every tool.
- **Path policy.** A path argument follows the common policy for a file: the
  lexical rules, hidden components, symlinks, special files, and the `.md`
  suffix, with the same order of checks and the same error codes. A regular
  file where a directory is needed returns `NOT_A_DIRECTORY`. The tool creates
  missing directories inside the inbox.
- **Text.** No text argument may contain NUL, CR, or U+FEFF; LF is the only
  line break, and a new note can never start with BOM bytes. Text beyond the
  [write-text limit](#initial-limits) or containing these characters returns
  `INVALID_ARGUMENT`.
- **Size.** The bytes written, including a BOM, must fit the readable file
  size limit, so that every proposal stays within the limit of the read
  tools. A larger result returns `FILE_TOO_LARGE`.
- **Writes.** A tool writes a hidden temporary file in the target's directory
  and then moves it into place, so readers see the old or the new file and
  never a partial one. One lock serializes all writes of the process, so a
  hash check and the write that depends on it cannot interleave with another
  write. A failed request leaves no new proposal behind. The [review
  script](design-decisions.md#vm-services-and-network) deletes reviewed
  proposals only while the service is stopped.
- **Results.** Both tools return `path`, the root-relative path of the
  proposal (`inbox/P`), and `content_sha256`, the SHA-256 of the bytes
  written. That is the hash `knowledge_read` reports for the proposal, so a
  following edit can pass it as `base_sha256` without reading again.
- **Annotations.** Both tools are annotated as not read-only, not
  idempotent, and closed-world. `knowledge_propose_note` is non-destructive,
  because it only adds a file. `knowledge_propose_edit` is destructive,
  because an edit of a pending proposal replaces its earlier text.
- **Descriptions.** Both descriptions say that the tool saves a proposal that
  the user reviews and merges. The description of `knowledge_propose_edit`
  also says to call it only when the user explicitly asks to change a note
  and has agreed to the change, never on the agent's own initiative, and to
  include surrounding lines when a text to replace is not unique. Both
  descriptions tell the agent to read `AGENTS.md` at the root before
  proposing, if it exists, and to follow its conventions for notes. Where
  `AGENTS.md` gives shell commands, the agent searches with the knowledge
  tools instead, and it skips Git steps and steps that run scripts. Both
  descriptions also say that note text refers to another note by its vault
  path, never by an `inbox/` path, because the proposal `inbox/P` becomes
  `P` when merged. On every transport, the
  descriptions of `knowledge_search`, `knowledge_read`, and `knowledge_list`
  say that notes under `inbox/` are unreviewed proposals, not yet part of
  the vault.

#### knowledge_propose_note

```text
knowledge_propose_note(path: str, content: str)
```

- `path` is the vault path that the new note should have, such as
  `Projects/Plan.md`. A path whose first component is `inbox` returns
  `INVALID_PATH`. Its parent directories need not exist in the vault, but
  those that exist must be real directories.
- An entry at `path` or at `inbox/<path>` returns `ALREADY_EXISTS`. The tool
  never overwrites: it moves the file into place with an operation that fails
  if the target exists, so a concurrent writer cannot be replaced either.
- After those checks, the tool deletes a base copy `inbox/.base/<path>` left
  from an earlier edit, so a proposal has a base copy exactly when it started
  from an edit of a vault note.
- `content` is written as UTF-8 with LF line endings and no BOM. It may be
  empty.

#### knowledge_propose_edit

```text
knowledge_propose_edit(path: str, base_sha256: str, edits: list[Edit])
Edit = {old: str, new: str}
```

- `path` names a vault note or a proposal:
  - **A vault note `P`.** If `inbox/P` exists, the result is
    `PROPOSAL_PENDING`, whose message says to edit the proposal under
    `inbox/` instead. This holds even when `P` does not exist in the vault,
    as for a new-note proposal.
    Otherwise the tool writes the base copy `inbox/.base/P` with the note's
    raw bytes, replacing any base copy left from an earlier proposal, and
    then writes the proposal `inbox/P`.
  - **A proposal `inbox/P`.** The tool replaces the proposal and keeps its
    base copy. A proposal for a new note has no base copy.
- `base_sha256` is 64 lowercase hexadecimal characters. It must equal the
  SHA-256 of the raw bytes that the tool loads from `path`, for example the
  `content_sha256` of an earlier read; otherwise the result is
  `STALE_CONTENT`. The tool loads the file with the bounded loader, so an
  oversized or invalid file returns `FILE_TOO_LARGE` or `INVALID_ENCODING`
  as for a read.
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
- The proposal keeps the note's encoding details. It starts with a BOM if
  the loaded bytes do. If the loaded bytes contain CRLF, every LF of the
  result is written as CRLF; otherwise LF stays. Bytes that contain both
  CRLF and an LF without a preceding CR return `MIXED_LINE_ENDINGS`, because
  either choice would change lines that the edits did not touch.

Checks run in this order: arguments, the path policy except existence,
`PROPOSAL_PENDING`, existence and the load, `STALE_CONTENT`,
`MIXED_LINE_ENDINGS`, the edits, and the result size.

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
`ALREADY_EXISTS`, `PROPOSAL_PENDING`, `STALE_CONTENT`, `TEXT_NOT_FOUND`,
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

Each operation reads the local vault checkout on a best-effort basis.
Concurrent edits may affect a read, and search locations can become stale
before a subsequent call. Read and info derive their content, line counts, and
hashes from the bounded bytes they load. There is no snapshot guarantee or
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

Write-tool tests use a temporary inbox and check that nothing outside it
changes. They cover a new note, an existing target, a path under `inbox/`, a
first edit with its base copy, an edit of a pending proposal, an edit of a
vault note that has one, a stale hash, a text to replace that is missing or
occurs more than once, edits that depend on earlier edits, BOM and CRLF
preservation, mixed line endings, NUL, CR, or U+FEFF in text, the text, edit,
and result limits, and reading proposals through the four read tools.
