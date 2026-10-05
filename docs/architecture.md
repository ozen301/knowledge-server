# Architecture overview

This document explains what the parts of knowledge-server do and how they work
together. It is an introduction for developers who are new to the code. The
[Phase 1 contract](phase-1-contract.md) defines the exact behavior, and the
[implementation tasks](implementation-tasks.md) track which parts exist. Terms
such as "local vault checkout" are defined in the [glossary](../GLOSSARY.md).

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
    adapter/server.py  - four tool wrappers, schemas, error translation
    __main__.py        - startup checks and stdio launch
    adapter/http*.py   - protected HTTP entry point and its launcher
    |
    v
Knowledge core
    core/paths.py      - which paths are visible
    core/reader.py     - read, list, info, and the shared note loader
    core/search.py     - literal search through ripgrep
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
  this, all four tools follow the same rules.
- **The vault is only read.** The vault owner synchronizes the local vault
  checkout with Git outside the server.

## Components

### Models, errors, and limits

`core/models.py` defines one request model and one result model for each tool,
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
the [contract values](phase-1-contract.md#initial-limits). Core operations
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
rejected; the [contract](phase-1-contract.md#configuration-and-common-policy)
lists the exact rules. Visibility is separate from readability: a listed note
can still be too large or not valid text.

### Read, list, info, and the shared loader: `core/reader.py`

`read_note()`, `list_directory()`, and `note_info()` implement three of the
four tools. For a note that is too large or is not valid text, `note_info()`
returns `readable=false` with the reason instead of an error; other failures,
such as a permission error, are still errors
([`knowledge_info`](phase-1-contract.md#knowledge_info)).

`load_note()` is the shared bounded loader. It opens each path component
relative to its parent without following symlinks, so a symlink or FIFO
swapped in after the policy check is rejected. It reads at most the file-size
limit plus one byte and checks that the text is valid UTF-8. Search uses the
same checks through `load_note_text()`, which also counts each chunk against
the search's byte budget and deadline. Because both use the same code, search
and read accept the same notes.

### Literal search: `core/search.py`

`search_notes()` implements `knowledge_search`: it finds lines that contain
the query as a literal phrase, not split into words and not ranked. It is an
`async` function, so that cancelling the request stops it, and it runs in two
stages:

1. **Load.** A worker thread discovers notes with the path policy and loads
   each one with the shared loader. Notes that cannot be read are skipped and
   counted.
2. **Match.** The loaded text goes, in sorted path order, to one ripgrep
   process on standard input. Search maps each reported line back to a note
   and line, and checks that it equals the text sent.

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
returned. The [contract](phase-1-contract.md#knowledge_search) gives the
details.

### MCP adapter

`__main__.py` is the entry point for the `knowledge-server` command and for
`python -m knowledge_server`. It loads the configuration, creates the
`PathPolicy`, and finds `rg` on `PATH`. If any of these fails, it logs a short
message to stderr and exits with status 1; otherwise it serves the tools over
stdio until the host closes stdin. Logs go to stderr, so stdout carries only
MCP messages.

`create_server()` in `adapter/server.py` registers the four tools on an SDK
`MCPServer`, with the request and result models' schemas as input and output
schemas. For each call, the request model validates the raw arguments, and the
tool calls one core function. Read, list, and info run in a worker thread;
search is awaited directly, so cancelling the request kills its ripgrep
process. A `KnowledgeError` becomes a tool error with the code and message
([format](phase-1-contract.md#error-and-change-behavior)). Any other exception
becomes `INTERNAL_ERROR`, and the log names only the exception type and source
location, because the exception's message can contain note text or paths.

The SDK's usual tool decorator builds its own lenient argument model, which
would convert `"5"` to `5` and ignore unknown fields. The contract requires
`INVALID_ARGUMENT` for such arguments, so the adapter builds the SDK's `Tool`
objects directly, with an argument model that passes the raw arguments
through to the request model. These SDK classes are not in its public exports,
so check the adapter when upgrading the SDK.

### Protected HTTP entry point

The `knowledge-server-http` command is a second way to reach the same four
tools: over HTTP instead of stdio. It listens on `127.0.0.1` (loopback), so
only programs in the same network namespace, such as others on the same
machine, can connect. The [web access plan](web-access.md) specifies the
remote route, the exact HTTP contract, and the VM services.

In the tested route, ChatGPT reaches the server through Cloudflare:

```text
ChatGPT
    -> Cloudflare Access (vault owner sign-in with Managed OAuth)
    -> Cloudflare Tunnel
    -> cloudflared on the Ubuntu VM
    -> knowledge-server-http on 127.0.0.1 on the same VM
```

Cloudflare Access acts as the OAuth server for ChatGPT's sign-in and serves
the OAuth discovery documents, so this server implements no OAuth.
cloudflared opens an outbound connection to Cloudflare Tunnel, so the router
needs no open inbound port. Cloudflare decrypts the traffic at its edge.

Each HTTP request passes a gate before any MCP handling, then reaches the
same tools, knowledge core, and path policy as stdio. In stateless mode, the
server keeps no MCP session between requests and answers with plain JSON
instead of an event stream:

```text
HTTP request
    -> gate: Host, then Origin, then Access assertion
    -> bounds: requests in progress, then body size and read time
    -> SDK Streamable HTTP at /mcp (stateless, JSON responses)
    -> the same four tools -> knowledge core -> root
```

- The `Host` header must name the configured public hostname or a loopback
  name. The `Origin` header, which browsers and some other clients send, must
  be absent or exactly match an entry in `allowed_origins`. That list names
  sites, not users, and authenticates nobody.
- The `Cf-Access-Jwt-Assertion` header, which Access adds to each request it
  lets through, must hold the Access assertion: a JSON Web Token (JWT) that
  Cloudflare signed for the configured Access application and the pinned
  owner subject, which is the vault owner's identifier, verified privately
  and fixed in the configuration. The server verifies the signature with
  Cloudflare's public keys. The OAuth access token in the `Authorization`
  header is meant for Cloudflare and never authenticates a request here.

A request that fails a check is refused with fixed text before the SDK sees
it. Only an authorized request counts toward the limit of requests in
progress and has its body read, within limits on body size and read time.

The launcher reads one private TOML file, named on the command line, that
holds the launch mode, the root, and the Access settings. It ignores
`KNOWLEDGE_ROOT` and `.env` files, so an environment prepared for stdio
cannot point the HTTP server at the real vault; the root still passes the
same validation in `config.py`. The mode has no default:

- **Synthetic mode** serves the invented notes. Before serving, the launcher
  compares the root with `synthetic-vault.json`, a packaged list of the
  sample notes and their SHA-256 digests, and does not start if anything
  differs. This check runs only at startup, and the tools read the directory
  live afterwards, so keep the directory dedicated to the invented notes
  while the server runs.
- **Vault mode** serves a local vault checkout or one subtree of it. The
  launcher refuses to start if it can write to the root, which catches a
  launch outside the read-only service configuration.

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

`deploy/` holds the server's systemd unit, which runs it as a dedicated
account with a read-only view of the file system, and an example
configuration. The [usage guide](usage.md#run-the-http-service-in-the-vm)
installs them with cloudflared and the vault synchronization.

The [HTTP contract](web-access.md#local-http-implementation-contract) gives
the exact settings, checks, and limits, and the [owner
identity](web-access.md#owner-identity) section explains how the owner subject
is established.

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
| reader.py, search.py                           |
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
   application and the pinned owner subject. Only then does the SDK see the
   request.
3. **Arguments.** Authentication shows who is calling, not that the
   arguments are safe. The adapter validates the raw arguments with the
   tool's request model, so the core receives a typed request and never sees
   HTTP headers, tokens, or raw MCP arguments.
4. **Files.** A valid request still gets no general file access. The core
   offers only the four read-only operations, `PathPolicy` decides which
   paths are visible, and `reader.py` and `search.py` enforce the content
   and resource limits.

On the stdio route, the vault owner's MCP host starts the server, so there is
no authentication; argument validation and the file limits are the same as
for HTTP. The [request gate](web-access.md#listener-and-request-gate) and
[assertion validation](web-access.md#assertion-validation) rules give the
exact Host, Origin, claim, signing-key, and request-limit checks.

## Design choices worth knowing

**Root-relative paths.** Callers and results use paths relative to the vault
root, with `/` as the separator, such as `Projects/roadmap.md`. Paths and
messages that the server generates never show the absolute path on the host.
This keeps host details private and gives the agent a path it can cite. Text
inside a note is returned as written, even if it contains a path.

**Numbered read lines.** Read results put each line's number in front of its
text, so that an agent can cite a line without counting lines. In the
[retrieval evaluation](retrieval-evaluation.md#known-weaknesses), hosts often
cited wrong lines when a read returned only the first and last line numbers.
Search matches carry their line number in a separate field.

**Git ignore rules do not hide notes.** The server reads the checkout's files
directly and does not use Git to decide what exists. Ignore rules control what
Git tracks, not who may read a file, so an ignored Markdown note is still
visible through all four tools.

**Symlinks are always rejected.** The policy rejects every symlink below the
root, even one that points to another note in the vault. A symlink can point
outside the vault, and it can be changed after the server checks it. A simple
rule that allows no symlinks avoids both problems, and the loader refuses to
follow a symlink even after the policy check has passed.

## Where to go next

- [Usage guide](usage.md): connecting the server to an MCP host.
- [Phase 1 contract](phase-1-contract.md): exact tool behavior, limits, and
  error codes.
- [Implementation plan](implementation-plan.md): decisions, their reasons, and
  the optional later possibilities.
- [Web access plan](web-access.md): the remote route for ChatGPT and the
  HTTP entry point's exact contract.
- [Implementation tasks](implementation-tasks.md): progress and the next task.
- [Repository guide](../AGENTS.md): development workflow and conventions.
