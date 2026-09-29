# Architecture overview

This document explains what the parts of knowledge-server do and how they work
together. It is an introduction for developers who are new to the code. The
[Phase 1 contract](phase-1-contract.md) defines the exact behavior, and the
[implementation tasks](implementation-tasks.md) track which parts exist. Terms
such as "local vault checkout" are defined in the [glossary](../CONTEXT.md).

## What the server does

knowledge-server lets an AI agent use a personal collection of Markdown notes.
The agent's host application (the MCP host) starts the server and calls its
tools. The agent can search the notes for a phrase, read a range of lines from
a note, list a directory, and get information about a note. Results name
notes and directories by their path in the vault, so the agent can cite its
source.

The server reads files from the local vault checkout and nothing else. It
never writes to notes, never runs Git commands or searches Git history, and
never runs code or follows links found inside a note. Note text is data that
the server returns, not instructions for the server.

## The big picture

The server is one Python package that runs as one process. Requests pass
through three layers:

```text
MCP host (for example, a desktop AI client)
    | starts the server as a subprocess; messages go over stdin/stdout
    v
MCP adapter                                              [planned: Task 5]
    adapter/server.py  - four tool wrappers, schemas, error translation
    __main__.py        - startup checks and stdio launch
    |
    v
Knowledge core                                   [mostly implemented]
    core/paths.py      - which paths are visible            [implemented]
    core/reader.py     - read, list, info                   [implemented]
    core/search.py     - literal search through ripgrep     [planned: Task 4]
    core/models.py, core/limits.py - shared data types and limits
    |
    v
Local vault checkout (the directory named by KNOWLEDGE_ROOT)
```

`config.py` sits beside these layers. It reads `KNOWLEDGE_ROOT` once when the
server starts.

The layers are separate for these reasons:

- **The adapter knows MCP; the core does not.** The core never imports the MCP
  SDK. It uses ordinary Python functions and data models, so tests can call it
  directly without starting a server.
- **The core owns every decision about the vault.** Which files are visible,
  how much text is returned, and which error applies are decided in the core.
  The adapter only translates between MCP messages and core calls. Because of
  this, all four tools follow the same rules.
- **The vault is only read.** The server treats the local vault checkout as
  read-only input. The vault owner synchronizes it with Git outside the server.

## Components

### Configuration: `config.py`

`load_config()` reads `KNOWLEDGE_ROOT` and returns a `Config` with the resolved
root directory. It raises `ConfigurationError` if the variable is missing or
does not name an absolute, readable directory. The server never guesses a
default root. Tests pass a dictionary instead of the real environment.

Used by: the planned startup code in `__main__.py`.

### Data models and errors: `core/models.py`

This module defines one request model and one result model for each tool, for
example `ReadRequest` and `ReadResult`. The models are Pydantic models that
reject unknown fields and wrong types. The field descriptions are written for
tool callers, because the adapter will build the MCP tool schemas from these
models.

The module also defines `KnowledgeError`, the one exception type that core
operations raise for an expected failure. Each error has a code from
`DomainErrorCode`, such as `NOT_FOUND`, and a fixed message. The message never
contains the path, note text, or other request data.

Used by: every other core module, and the planned adapter.

### Limits: `core/limits.py`

`Limits` holds every resource limit, such as the largest readable file and the
most lines one read returns. `DEFAULT_LIMITS` contains the values from the
contract. Core operations accept a `Limits` argument, so tests can use small
limits instead of large test files.

Used by: the request models and the core operations.

### Path and visibility policy: `core/paths.py`

This module is the most important one to understand first. `PathPolicy`
decides which paths a caller can see, and every tool uses it. Its main
methods:

- `resolve(path, target)` checks a root-relative path such as
  `Projects/roadmap.md`. It returns a `ResolvedPath` if the path is visible and
  has the expected kind (file or directory). Otherwise it raises a
  `KnowledgeError` with the matching code.
- `discover_immediate(path)` lists the visible children of a directory. It
  checks each child with `resolve`, so listing and direct access always agree
  on which paths are visible. A read also checks the note's size and encoding,
  so a listed note can still be too large or invalid to read.

The policy makes these files visible: regular files with a `.md` suffix in
directories that are not hidden. It rejects hidden names (starting with `.`),
symlinks, special files such as FIFOs, and paths that could leave the root,
such as `../secret.md`.

Used by: `core/reader.py` and the planned `core/search.py`.

### Read, list, and info: `core/reader.py`

This module implements three of the four tools:

- `read_note()` returns a range of whole lines from a note.
- `list_directory()` returns one page of a directory's visible children.
- `note_info()` returns a note's size, modification time, line count, and
  hash. It also works for a note that is too large or is not valid text, and
  then reports why the note cannot be read.

`load_note()` is the shared loader for note contents. It reads at most the
file-size limit plus one byte, checks that the text is valid UTF-8, and splits
the text into lines. Search will reuse it so that search and read accept the
same notes.

Used by: the planned adapter.

### Literal search: `core/search.py` (planned)

Task 4 adds `knowledge_search`. It will find notes with the shared policy,
check them with the same content rules as reading, and run ripgrep to find
lines that contain the query as a literal phrase. Matching ignores letter
case unless the caller asks for case-sensitive search. A query such as
`ECC Ryzen` matches that phrase only; it is not split into words and is not a
semantic search.

### MCP adapter: `adapter/server.py` and `__main__.py` (planned)

Task 5 adds the MCP layer. `__main__.py` will check the configuration and
that ripgrep is available, and then start the server over stdio. If either
check fails, the server exits with an error. `adapter/server.py` will register
the four tools `knowledge_search`, `knowledge_read`, `knowledge_list`, and
`knowledge_info`. Each tool wrapper will call one core function and convert a
`KnowledgeError` into an MCP tool error. The adapter will also run slow work
without blocking the protocol loop, pass cancellation to a running search, and
turn an unexpected exception into a safe `INTERNAL_ERROR`.

## How a read request flows

This example follows one `knowledge_read` call. The steps through the core
exist now; the MCP steps are planned.

1. **Startup.** The MCP host starts the server with `KNOWLEDGE_ROOT` set to the
   local vault checkout. `load_config()` checks the root once, the server
   checks that ripgrep is available, and it creates one `PathPolicy` for the
   root.
2. **Request.** The agent calls `knowledge_read` with
   `path="Projects/roadmap.md"` and `end_line=20`. The adapter turns the
   arguments into a `ReadRequest`. The model rejects invalid arguments, such as
   a line range that ends before it starts.
3. **Path check.** `read_note()` calls `policy.resolve()`. The policy checks
   the path's form, then hidden names, then the filesystem. If any check
   fails, the request stops here with a `KnowledgeError`.
4. **Load.** `load_note()` opens the file one path component at a time and
   does not follow symlinks. It reads a bounded number of bytes and splits the
   text into lines.
5. **Result.** `read_note()` collects lines 1-20 until it reaches the
   requested end, the end of the note, or the content-size limit. The
   `ReadResult` contains the text, the returned line numbers, `next_line` for
   continuing the read, `truncated` if the limit stopped the read early, and a
   hash of the file.
6. **Response.** The adapter sends the result to the host. If the core raised
   a `KnowledgeError`, the adapter sends an MCP tool error with the error code
   and its fixed message instead.

List and info requests follow the same path check and error handling. The
contract lists the [limit values](phase-1-contract.md#initial-limits) and
the [error codes](phase-1-contract.md#error-and-change-behavior).

## Design choices worth knowing

### Root-relative paths

Callers and results use paths relative to the vault root, with `/` as the
separator, such as `Projects/roadmap.md`. Paths and messages that the server
generates never show the absolute path on the host, for example
`/home/user/vault/Projects/roadmap.md`. This keeps host details private and
gives the agent a path it can cite. Text inside a note is returned as written,
even if it contains a path. See
the [common policy](phase-1-contract.md#configuration-and-common-policy) for
the exact path rules.

### Git ignore rules do not hide notes

The server reads the files in the checkout directly. It does not use Git to
decide what exists. A Markdown note listed in `.gitignore` is still visible,
because ignore rules control what Git tracks, not who may read a file. Search
through ripgrep must therefore turn off ripgrep's default use of ignore files,
so that all four tools agree on what is visible.

### Symlinks are always rejected

The policy rejects every symlink below the root, even one that points to
another note in the vault. A symlink can point outside the vault, and it can
be changed after the server checks it. A simple rule that allows no symlinks
avoids both problems. For the same reason, `load_note()` does not trust the
earlier check: it refuses to follow a symlink when it opens the file. The
[contract](phase-1-contract.md#configuration-and-common-policy) lists all
rejected path forms.

## Where to go next

- [Glossary](../CONTEXT.md): the project's terms.
- [Phase 1 contract](phase-1-contract.md): exact tool behavior, limits, and
  error codes.
- [Implementation plan](implementation-plan.md): decisions, their reasons, and
  later milestones.
- [Implementation tasks](implementation-tasks.md): progress and the next task.
- [Repository guide](../AGENTS.md): development workflow and conventions.
