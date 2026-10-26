# Architecture overview

This document explains what the parts of knowledge-server do and how they work
together. It is an introduction for developers who are new to the code. The
[tool contract](tool-contract.md) defines the exact behavior. Terms such as
"local vault checkout" are defined in the [glossary](../GLOSSARY.md).

## What the server does

knowledge-server lets an AI agent use a personal collection of Markdown notes.
The agent's host application (the MCP host) starts the server and calls its
tools. The agent can search the notes for a phrase, read a range of lines from
a note, and list a directory. Results name notes and directories by their
path in the vault, so the agent can cite its source.

The server reads notes from the local vault checkout. It never changes a
note, never searches Git history, and never runs code or follows links found
inside a note. Note text is data that the server returns, not instructions
for the server. Over HTTP, when its configuration enables them, three more
tools let an agent propose a new note or an edit, and list the pending
proposals. Each proposal is a Git branch of the vault remote, which the vault
owner reviews and merges into `main`.

## The big picture

The server is one Python package that runs as one process. It serves the
tools over stdio and through a [protected HTTP entry
point](#protected-http-entry-point) on loopback. Requests pass
through three layers. This diagram shows the stdio route; HTTP requests
enter through `adapter/http*.py` and use the same tools, core, and policy:

```text
MCP host (for example, a desktop AI client)
    | starts the server as a subprocess; messages go over stdin/stdout
    v
MCP adapter
    adapter/server.py  - tool wrappers, schemas, error translation
    __main__.py        - startup checks and stdio launch
    adapter/http*.py   - protected HTTP entry point and its launcher
    |
    v
Knowledge core
    core/paths.py      - which paths are visible
    core/reader.py     - read, list, and the shared note loader
    core/search.py     - literal search through ripgrep
    core/writer.py     - write proposals
    core/git.py        - Git work for proposals: commits, pushes, listing
    core/models.py, core/limits.py - shared data types and limits
    |
    v
Local vault checkout (the directory named by KNOWLEDGE_ROOT)
```

`config.py` sits beside these layers and validates the root once at startup;
it never guesses a default root. For stdio, the root comes from the
`KNOWLEDGE_ROOT` environment variable. The HTTP launcher ignores that
variable: it takes the root from its explicit configuration file and passes
it to the same validation.

The layers are separate for these reasons:

- **The adapter knows MCP; the core does not.** The core never imports the MCP
  SDK. It uses ordinary Python functions and data models, so tests can call it
  directly without starting a server, and a second entry point can reuse it.
- **The core owns every decision about the vault.** Which files are visible,
  how much text is returned, and which error applies are decided in the core.
  The adapter only translates between MCP messages and core calls. Because of
  this, all tools follow the same rules.
- **The vault is only read.** For proposals, the server writes only in a
  Git repository of its own, from which it pushes proposal branches. A cron
  job synchronizes the local vault checkout with Git, and the vault owner
  merges proposals; both happen outside the server.

## Components

### Models, errors, and limits

`core/models.py` defines a request model and a result model for each tool,
for example `ReadRequest` and `ReadResult`. These Pydantic models reject
unknown fields and wrong types. The adapter builds the MCP tool schemas from
them, so their field descriptions are written for tool callers.

Core operations raise one exception type for an expected failure,
`KnowledgeError`, with a code such as `NOT_FOUND` and a fixed message that
never contains the path, note text, or other request data. For
`INVALID_ARGUMENT`, the message is built from the request model's schema, so
it names each rejected argument and its accepted values and follows the
limits automatically.

`core/limits.py` holds every resource limit in `Limits`; `DEFAULT_LIMITS` has
the [contract values](tool-contract.md#initial-limits). Core operations
accept a `Limits` argument, so tests can use small limits instead of large
test files.

### Path and visibility policy: `core/paths.py`

This module is the most important one to understand first. `PathPolicy`
decides which paths a caller can see, and every tool uses it. `resolve()`
checks a root-relative path that a caller supplies, and `visible_child()`
checks an entry found by scanning a directory. Because `visible_child()` uses
`resolve()`, scanning and direct access always agree on which paths are
visible.

Only regular `.md` files in non-hidden directories are visible. Hidden names,
symlinks, special files such as FIFOs, and paths that could leave the root are
rejected; the [contract](tool-contract.md#configuration-and-common-policy)
lists the exact rules. Visibility is separate from readability: a listed note
can still be too large or not valid text.

### Read, list, and the shared loader: `core/reader.py`

`read_note()` and `list_directory()` implement two of the three read tools.

`load_note()` is the shared bounded loader. It opens each path component
relative to its parent without following symlinks, so a symlink or FIFO
swapped in after the policy check is rejected. It reads at most the file-size
limit plus one byte and checks that the text is valid UTF-8. Search uses the
same checks through `load_note_text()`, which also counts each chunk against
the search's byte budget and deadline. Because both use the same code, search
and read accept the same notes.

### Write proposals: `core/writer.py` and `core/git.py`

`propose_note()` and `propose_edit()` implement the two write tools.
`ProposalBranches` in `core/git.py` does their Git work and also lists
proposals and reads notes from a branch. It works in the server's own bare
Git repository, `/var/lib/knowledge-server/vault.git` under the service unit,
which the launcher creates if it is missing. The server may only read the
local vault checkout and so cannot commit there. Instead, at startup and
before each new proposal, it copies the checkout's commits into its own
repository with a local `git fetch`. The repository holds a copy of every
object it uses, so garbage collection in the checkout cannot remove them.

Each write needs a commit to start from. A new proposal starts from the
commit that the local vault checkout has checked out (`HEAD`). A revision
starts from the newest commit of its proposal branch, which the server first
fetches from the vault remote. `ProposalBranches` checks the path with
`PathPolicy.parse()`, checks each entry on the path in that commit, and loads
a note with the size and encoding checks of the shared loader.

An edit applies exact text replacements to the note's text as the read tools
see it, and keeps the note's BOM and line endings. The writer then builds a
tree in a temporary index, creates the commit with the agent's summary in its
message, and pushes it with a lease: a new branch only if no branch of that
name exists, and a revision only if the branch tip has not moved. One lock
serializes all proposal operations of the process, including the fetches of
listing and reading, because they update the same refs. Git runs as a
subprocess with bounded output, deadlines, and no user or system
configuration. It reaches the vault remote by path, over SSH with the
server's key, or over HTTPS with the server's credential file, as the [HTTP
contract](http-contract.md#launch-modes) describes. The
[contract](tool-contract.md#write-proposals) gives the rules and their
order.

### Literal search: `core/search.py`

`search_notes()` implements `knowledge_search`: it finds lines that contain
any of the request's queries, each a literal phrase, not split into words and
not ranked. It is an `async` function, so that cancelling the request stops
it, and it runs in two stages:

1. **Load.** A worker thread discovers notes with the path policy and loads
   each one with the shared loader. Notes that cannot be read are skipped and
   counted.
2. **Match.** The loaded text, converted to Unicode NFC like the queries,
   goes in sorted path order to one ripgrep process on standard input. Search
   maps each reported line back to a note and line, and checks that it equals
   the text sent.

ripgrep never opens a vault file. A note replaced by a symlink after the
policy check therefore cannot make ripgrep read outside the vault, and a note
edited during the search cannot make a result disagree with the text that was
checked. ripgrep's own ignore-file handling never applies either. Because the
input is in result order, ripgrep stops as soon as one more hit than requested
is found.

Search enforces a deadline and budgets for visited entries, loaded bytes, and
ripgrep output. On a timeout, a cancellation, or an exceeded budget, it kills
and reaps the ripgrep process. A filesystem call that is already blocked
cannot be interrupted, so the loading thread can outlive a search that has
returned. The [contract](tool-contract.md#knowledge_search) gives the
details.

### MCP adapter

`__main__.py` is the entry point for the `knowledge-server` command and for
`python -m knowledge_server`. It loads the configuration, creates the
`PathPolicy`, and finds `rg` on `PATH`. If any of these fails, it logs a short
message to stderr and exits with status 1; otherwise it serves the tools over
stdio until the host closes stdin. Logs go to stderr, so stdout carries only
MCP messages.

`create_server()` in `adapter/server.py` registers the three read tools on an
SDK `MCPServer`, and the three proposal tools when its caller passes the vault
remote; only the HTTP launcher does. The request and result models' schemas
become the input and output schemas. For each call, the request model validates
the raw arguments, and the tool calls one core function. Read, list, and the
proposal tools run in a worker thread. Search is awaited directly, so cancelling
the request kills its ripgrep process. A `KnowledgeError` becomes a tool error
with the code and message
([format](tool-contract.md#error-and-change-behavior)). Any other exception
becomes `INTERNAL_ERROR`, and the log names only the exception type and source
location, because the exception's message can contain note text or paths.

The SDK's usual tool decorator builds its own lenient argument model, which
would convert `"5"` to `5` and ignore unknown fields. The contract requires
`INVALID_ARGUMENT` for such arguments, so the adapter builds the SDK's `Tool`
objects directly, with an argument model that passes the raw arguments
through to the request model. These SDK classes are not in its public exports,
so check the adapter when upgrading the SDK.

### Protected HTTP entry point

The `knowledge-server-http` command is a second way to reach the same tools:
over HTTP instead of stdio. Only this entry point can serve the write tools. It
listens on `127.0.0.1` (loopback), so only programs in the same network
namespace, such as others on the same machine, can connect. The [HTTP
contract](http-contract.md) specifies its exact checks, and the [design
decisions](design-decisions.md#why-this-remote-route) explain the remote route
through Cloudflare and the VM services.

Each HTTP request passes a gate before any MCP handling, then reaches the
same tools, knowledge core, and path policy as stdio. In stateless mode, the
server keeps no MCP session between requests and answers with plain JSON
instead of an event stream. [Trust boundaries](#trust-boundaries) explains
the checks:

```text
HTTP request
    -> gate: Host, then Origin, then Access assertion
    -> bounds: requests in progress, then body size and read time
    -> SDK Streamable HTTP at /mcp (stateless, JSON responses)
    -> the same tools -> knowledge core -> root
```

The launcher reads one private TOML file, named on the command line, that
holds the launch mode, the root, and the Access settings; it ignores
`KNOWLEDGE_ROOT` and `.env` files. **Synthetic mode** serves only an exact
copy of the invented sample notes, and **vault mode** serves a local vault
checkout or a subtree of it, and refuses to start if it can write to the
root. The HTTP contract's [launch modes](http-contract.md#launch-modes)
give the rules.

The code is in `adapter/`:

- `http_main.py` starts the server. It loads the configuration with
  `http_config.py`, runs the mode's root check (in synthetic mode, the one in
  `synthetic.py`), and starts Uvicorn, the web server, on `127.0.0.1` without
  proxy-header interpretation, so a request cannot change its apparent
  client address or scheme.
- `http.py` creates the SDK's ASGI application (ASGI is Python's standard
  interface between web servers and applications) and wraps all of it, on
  every path, in `HTTPApplication`, the gate. The gate also applies the
  request bounds and adds `Cache-Control: no-store` to its responses.
- `http_auth.py` holds the bounded, rate-limited signing-key cache
  (`CachedKeys`) and the assertion check (`AssertionVerifier`, built on
  PyJWT), which returns only yes or no.
- `http_logging.py` restricts the HTTP process's log to fixed startup and
  event categories and each request's method, status, and latency. It drops
  all other diagnostics, which could contain tokens, headers, queries, or
  note text.

`deploy/` holds the server's systemd unit, which runs it as a dedicated account
with a read-only view of the file system, and an example configuration. The
[guide for web clients](use-with-web-clients.md) installs them with cloudflared
and the vault synchronization. The unit gives the server one state
directory, `/var/lib/knowledge-server`, for its repository for proposals.

## Trust boundaries

A remote request gains trust in steps. It moves through three zones, and a
boundary of checks follows each zone. At the first boundary, the request is
authenticated twice: by Cloudflare Access at the edge and then by this
server. At the second, the adapter validates the tool arguments. At the
third, the core limits what the request can read:

```text
+------------------------------------------------+
| ZONE 1: EXTERNAL, UNTRUSTED                    |
|                                                |
| ChatGPT -> internet -> Cloudflare edge         |
+-----------------------+------------------------+
                        |
                        |  Authentication 1: Cloudflare Access (OAuth)
                        |  grants access to the Access application
                        |
                        |  Authentication 2: HTTPApplication verifies
                        |  the Access assertion (JWT)
========================|=========================
                        | authenticated request
                        v
+------------------------------------------------+
| ZONE 2: PROTOCOL AND ADAPTER                   |
|                                                |
| MCP SDK -> adapter/server.py                   |
+-----------------------+------------------------+
                        |
                        |  strict request model
========================|=========================
                        | typed request
                        v
+------------------------------------------------+
| ZONE 3: KNOWLEDGE CORE                         |
|                                                |
| reader.py, search.py, writer.py                |
+-----------------------+------------------------+
                        |
                        |  PathPolicy, content and resource limits
========================|=========================
                        | visible note, bounded read
                        v
                Files under the root
```

Passing one check does not skip the next:

1. **Authentication 1: Cloudflare Access.** At the edge, Access
   authenticates the client, such as ChatGPT, with OAuth and lets a request
   through only if the owner-only policy allows it. It then adds the Access
   assertion to the request.
2. **Authentication 2: `HTTPApplication`.** Cloudflare Tunnel and
   cloudflared only carry the request to the VM; they do not authenticate the
   client. Any other program in the same network namespace, such as one on
   the VM, can also connect to the loopback listener, so a request there did
   not necessarily come through Access. The gate accepts a request only if
   its Access assertion is signed by Cloudflare for the configured Access
   application and the pinned [owner subject](http-contract.md#owner-identity),
   the vault owner's identifier. Only then does the SDK see the request.
3. **Arguments.** Authentication shows who is calling, not that the
   arguments are safe. The adapter validates the raw arguments with the
   tool's request model, so the core receives a typed request and never sees
   HTTP headers, tokens, or raw MCP arguments.
4. **Files.** A valid request still gets no general file access. The core
   offers only the three read operations and the proposal operations.
   `PathPolicy` decides which paths are visible, also in proposal commits,
   and `reader.py`, `search.py`, `writer.py`, and `git.py` enforce the
   content and resource limits. The service unit limits writes to the
   server's own directory, `/var/lib/knowledge-server`.

On the stdio route, the vault owner's MCP host starts the server, so there is
no authentication; argument validation and the file limits are the same as
for HTTP. The [request gate](http-contract.md#listener-and-request-gate) and
[assertion validation](http-contract.md#assertion-validation) rules give the
exact Host, Origin, claim, signing-key, and request-limit checks.
