# Phase 1 tool contract

Status: agreed implementation contract, updated 2026-09-22. Tasks 1 and 2
have established the package scaffold, SDK compatibility check, and shared
configuration/models/path policy; no Phase 1 operations or MCP tools are
implemented. Changes should update this document and the corresponding tests
together.

Before Task 3, decide the exact BOM/newline representation and whether
`next_line` is present after a fully satisfied range when more file content
exists. Before Task 4, decide the exact snippet-window behavior, including a
match longer than the snippet limit. Do not let an implementation choice
silently establish these contract details.

## Configuration and common policy

- `KNOWLEDGE_ROOT` is required and points to the local Git checkout of the knowledge vault. It must identify an existing readable directory and is resolved once on startup. The server reads its files directly; it does not require or inspect Git metadata and does not run Git commands. Example value: `/path/to/knowledge-vault`; replace it with a local absolute path. Never default to the process working directory, home directory, or NAS-hosted Git remote.
- Root visibility is all non-hidden Markdown notes, as confirmed by the user. Tool arguments cannot expand access beyond the configured root and common policy.
- API paths use `/`, relative to the configured root. Empty string means the root for list/search only. Accept spaces and Unicode. Preserve case and Unicode spelling; do not URL-decode paths.
- Reject absolute paths, Windows drive/UNC paths, backslashes, NUL/control characters in Unicode category `Cc`, and `.` or `..` path components. Reject repeated or empty internal components and multiple trailing slashes. A single trailing slash is accepted only for directory-capable inputs and requires the final target to be a directory; a trailing slash on a file-only input is invalid. Empty path is accepted only for directory-capable inputs. Do not use string-prefix tests for containment; inspect components with `lstat` before following anything and check resolved ancestry.
- Reject every symlink below the configured root, including links targeting another in-root file. Reject special files such as FIFOs and devices. Only regular files and real, non-symlink directories are eligible.
- Reject any hidden component (starting with `.`), including `.git` and `.obsidian`. Files must have a case-insensitive `.md` suffix. Do not expose other file names through listing.
- `.gitignore`, `.ignore`, global Git ignores, and ripgrep user configuration must not silently change visibility. An eligible ignored Markdown note remains accessible through every tool. Document this explicitly.
- A listing may show visible ordinary subdirectories even when they contain no eligible notes. It must not recurse into them automatically.
- File contents: strict UTF-8, optional UTF-8 BOM accepted, no NUL bytes. Normalize CRLF to LF for line handling. A final newline does not create an extra empty line. An empty file has zero lines.
- Enforce the readable/searchable file-size limit below. Read at most that amount plus one sentinel byte rather than trusting a prior stat alone. Larger files may be listed and inspected but are not read or searched.
- Tool results and tool errors contain no host absolute paths, command lines, or raw subprocess stderr. This applies to server-generated metadata; do not rewrite path-like text authored inside a note.
- Logs go to stderr and exclude secrets, note contents, and query text, including values embedded in exception messages. Stdout carries only protocol output. No telemetry exporter is configured by the application.

The shared policy determines path visibility; content/size checks determine readability. Listing/info can reveal that a visible file is too large, but hidden or disallowed paths are never exposed.

The configured root must be an explicit absolute existing directory that is readable and searchable. A root symlink may resolve during startup. For a relative API path, validate its lexical form before checking hidden components or filesystem existence. For file-only requests, reject a final non-Markdown suffix as `UNSUPPORTED_TYPE` before any existence check. For requests accepting either a file or directory, such as search, inspect the target first: directories are accepted regardless of suffix, while ordinary non-Markdown files are `UNSUPPORTED_TYPE`. A Markdown directory passed to a file-only request is `NOT_A_FILE`; any regular file passed to a directory target is `NOT_A_DIRECTORY`. Missing otherwise eligible targets are `NOT_FOUND`.

## Initial limits

These values are enforced in Phase 1 and reviewed after the retrieval evaluation in Task 6a. Define each value once in code and reuse it in validation and execution. Change the definition, this table, and affected tests together when evaluation justifies a different value. Tests may inject smaller limits to exercise boundaries without large or slow fixtures.

| Setting | Initial value |
|---|---|
| Maximum readable/searchable file size | 1 MiB |
| Maximum query length | 512 Unicode code points |
| Search results: default / maximum | 20 / 50 |
| Maximum snippet length | 300 Unicode code points |
| Read range: default / maximum | 200 / 200 lines |
| Maximum returned read content | 32 KiB of UTF-8 text |
| Directory page: default / maximum | 100 / 200 entries |
| Search deadline for the full operation | 10 seconds |
| Maximum filesystem entries visited per search | 10,000 |
| Maximum source bytes per search | 16 MiB |
| Maximum subprocess output per search | 4 MiB |
| Maximum immediate entries scanned per directory listing | 10,000 |

The filesystem-entry budgets count every directory entry inspected before
visibility filtering, but excluded directories are not traversed. The
source-byte budget counts raw bytes actually loaded while validating candidate
files, including sentinel bytes and bytes from files later skipped. The
subprocess-output budget counts stdout and stderr together across all batches.

## Tools

All numbers are strict integers (booleans and numeric strings are invalid). Reject invalid bounds rather than silently widening requests. Return object-shaped typed results. Use the SDK's typed-output support to produce successful structured content and its equivalent JSON text representation. Map domain failures to MCP tool errors with `isError=true`; let the SDK handle malformed protocol requests. [Official tool specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools), [SDK structured output](https://py.sdk.modelcontextprotocol.io/servers/structured-output/).

### knowledge_search

```text
knowledge_search(
    query: str,
    path: str = "",
    max_results: int = 20,
    case_sensitive: bool = false,
    mode: Literal["literal"] = "literal"
)
```

- `path` may name an eligible file or visible directory; directory search is recursive.
- Query is a literal substring within one line. Preserve spaces; reject whitespace-only queries, newlines, NULs, and queries exceeding the query-length limit. No regex, query language, stemming, or implicit word splitting.
- Case behavior follows ripgrep's Unicode-aware case-insensitive matching unless `case_sensitive=true`; include non-ASCII cases in fixtures. No Unicode normalization is performed.
- One hit per matching line, even with multiple occurrences on that line. Sort paths case-sensitively by Unicode code-point order, then by line number. No relevance score.
- `max_results` must be at least 1 and at most the search-result maximum. Fetch one additional hit to determine result-limit truncation.
- Snippet: a window within the snippet-length limit centered on the first match's start; include `snippet_truncated`. Paths and line numbers allow a full read.
- Search does not paginate initially: narrow the path or query when truncated.
- Missing/disallowed explicit targets are errors. During recursive search, unreadable, invalid-encoding, oversized, or concurrently removed files are skipped and counted by reason, without exposing hidden filenames.

Known limitation: visually identical text can use different Unicode representations, such as `é` versus `e` followed by a combining accent, or `が` versus `か` followed by a combining voiced mark. These representations can fail to match. Half-width and full-width forms also remain distinct. NFC-equivalent matching is an important deferred feature described in the [implementation plan](implementation-plan.md#deferred-retrieval-decisions); the initial tests document these missed matches.

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

`truncated` means more matches were found than returned. `incomplete` means eligible content could not all be searched because files were skipped; it is independent of `truncated`. Ordinary policy exclusions do not count as skipped. Do not claim an exact total hit count.

Count a discovered file that disappears or becomes inaccessible before it can be read as `unreadable`. An explicitly requested file that is missing produces `NOT_FOUND`; a permission failure produces `ACCESS_DENIED`.

Use ripgrep with fixed-string JSON output and argument-list subprocess calls (`shell=False`). Pass the query as a value to `-e`; never interpolate it into a command. Feed only policy-approved regular files to ripgrep, in bounded batches. Use explicit options/environment so user config and ignore files cannot alter behavior. Validate text eligibility consistently with the reader before emitting hits. Use a shared deadline and stream/cap subprocess output; never capture arbitrarily large output and truncate afterward. Exit code 1 means no matches, not a failure. Kill and reap children on timeout/cancellation. The [ripgrep guide](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md) is the upstream reference.

Enforce the search deadline, visited-entry, source-byte, and subprocess-output budgets from the initial limits table. Exceeding any budget is a `SEARCH_LIMIT_EXCEEDED` error, without presenting partial results as complete. Only the user-requested hit limit produces a successful truncated response.

### knowledge_read

```text
knowledge_read(path: str, start_line: int = 1, end_line: int | null = null)
```

- Line numbers are one-based and inclusive. `end_line=null` requests the default read range from `start_line`. Explicit ranges must fit the maximum read range.
- Negative/zero line numbers, reversed ranges, and oversized requested ranges are invalid. Clamp the actual end to EOF.
- Starting after EOF succeeds with empty content and null actual line bounds. Return `total_lines` so the caller can recover.
- Return whole lines, with normalized newlines, within the returned-content byte limit. If necessary, stop before a line and provide continuation. If the first requested line alone exceeds the cap, return `LINE_TOO_LONG` rather than silently slicing it.
- `next_line` points to the next unread line in the file, or null at EOF. `truncated` is true only when the byte limit prevented fulfilling the requested range; selecting a small range is not itself truncation.
- Include SHA-256 of the loaded raw bytes as `content_sha256`. This identifies the bytes used for this response, including uncommitted edits; it does not guarantee a filesystem snapshot.

Result fields: `path`, `content`, `start_line` and `end_line` (actual bounds or null), `total_lines`, `next_line`, `truncated`, `content_sha256`.

### knowledge_list

```text
knowledge_list(path: str = "", offset: int = 0, limit: int = 100)
```

- Immediate children only, ordered case-sensitively by root-relative path in Unicode code-point order. Return root-relative paths and `kind` (`file` or `directory`). Apply visibility rules before pagination.
- `offset >= 0`; `limit` must be at least 1 and at most the directory-page maximum. Offset beyond the list gives an empty result. This is best-effort pagination of the live directory; edits between calls may shift entries.
- Result fields: `path`, `entries`, `next_offset` (null at end), `truncated` (more entries exist).
- Enforce the immediate-entry scan limit; beyond that return `DIRECTORY_LIMIT_EXCEEDED`. Do not build an unbounded directory listing.

### knowledge_info

```text
knowledge_info(path: str)
```

- Files only. Return `path`, `size_bytes`, `modified_at` (UTC RFC 3339), `line_count`, `content_sha256`, and `readable`.
- For a readable file, compute line count and hash from the same bounded byte load. For oversized/invalid-text files, return `readable=false`, null line count/hash, and `unreadable_reason` (`too_large` or `invalid_text`). For ordinary readable files, `unreadable_reason=null`.
- Permissions failures are errors. Modification time is filesystem metadata, not a guarantee that every writer preserves timestamps.

## Error and change behavior

Domain errors have `code` and a short safe `message`. Codes: `INVALID_ARGUMENT`, `INVALID_PATH`, `NOT_FOUND`, `ACCESS_DENIED`, `UNSUPPORTED_TYPE`, `NOT_A_FILE`, `NOT_A_DIRECTORY`, `FILE_TOO_LARGE`, `INVALID_ENCODING`, `LINE_TOO_LONG`, `SEARCH_LIMIT_EXCEEDED`, `DIRECTORY_LIMIT_EXCEEDED`, `SEARCH_FAILED`, `INTERNAL_ERROR`.

Check path policy before reporting existence so hidden/disallowed paths do not become existence probes. Apply these mappings consistently:

- Malformed paths return `INVALID_PATH`.
- Hidden components and symlinks return `ACCESS_DENIED`.
- When an operation requires a file, a final path component without a
  case-insensitive `.md` suffix returns `UNSUPPORTED_TYPE`, checked before
  existence. A request that accepts either a file or directory, such as search,
  accepts visible directories regardless of suffix and returns
  `UNSUPPORTED_TYPE` for an identified non-Markdown regular file. A
  directory-only request, such as list, returns `NOT_A_DIRECTORY` for every
  regular file.
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
nonzero exit status, not a live half-working server.

Use shared error translation in the MCP adapter for all four tools. Translate unexpected application exceptions explicitly into a generic `INTERNAL_ERROR`; the SDK's default error response does not supply this domain code. Construct safe messages from known conditions rather than returning raw exception text. Diagnostic logs must obey the content restrictions above, including for exceptions containing source bytes. Cancellation must still propagate to active work. [SDK error handling](https://py.sdk.modelcontextprotocol.io/servers/handling-errors/).

Each operation reads the local vault checkout on a best-effort basis. Concurrent edits may affect a read, and search locations can become stale before a subsequent call. Read and info derive their content, line counts, and hashes from the bounded bytes they load. There is no snapshot guarantee or general concurrent-edit detection. Do not cache content between requests. Callers can repeat search/read if a note changes.

## Required behavioral examples

Fixtures must exercise ordinary notes, empty files, CRLF/BOM, Unicode content and filenames, spaces/leading dashes in names, duplicate line matches, ignored Markdown, hidden files, traversal, symlinks inside/outside the root, root-prefix collisions, wrong types, invalid UTF-8/NULs, oversized files/lines, output truncation, and missing files.

Every tool must reject access to hidden and symlinked content; search/list must not reveal it. Tests use a temporary synthetic vault and an outside sentinel file. No tests depend on the user's real notes. Cover subprocess arguments, exit codes, timeout cleanup, and bounded output as well as ordinary search success.
