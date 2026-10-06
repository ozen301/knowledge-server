# HTTP contract

Status: implemented and deployed, updated 2026-10-07. This document specifies
the protected HTTP entry point, `knowledge-server-http`: its configuration,
launch modes, request checks, assertion and key rules, and logging.
`tests/test_http.py` checks it offline. The [design
decisions](design-decisions.md#why-this-remote-route) explain the route that
it serves, and the [guide for web clients](use-with-web-clients.md) installs
and runs it. The [tool contract](tool-contract.md) defines the tools
themselves.

## Entry point

The entry point serves the four read tools of the [tool
contract](tool-contract.md) at `/mcp` over the SDK's Streamable HTTP
transport, in stateless mode with JSON responses. Their schemas and
annotations are semantically equal to the stdio server's. When
`write_proposals` is enabled, it also serves the two [write
tools](tool-contract.md#write-proposals), which stdio does not. It adds no
`search` or `fetch` wrapper tools; OpenAI's [developer-mode
guide](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
does not require them.

The SDK's OAuth authentication settings stay unconfigured: its bearer
authentication would read Cloudflare's opaque token from the `Authorization`
header, and it can publish OAuth metadata that competes with the metadata
Access serves. Instead, a gate around the whole ASGI application checks every
request, as the [request gate](#listener-and-request-gate) defines.

## Launch configuration

`knowledge-server-http --config /absolute/private/config.toml` reads one
explicit TOML file, given by absolute path, of at most 16 KiB. It ignores
`KNOWLEDGE_ROOT`, proxy environment variables, and `.env` files.

| Field | Default | Rule |
|---|---|---|
| `mode` | required | `"synthetic"` or `"vault"`; see [launch modes](#launch-modes). |
| `root` | required | Absolute path to an existing readable directory: the directory the tools expose. |
| `team_domain` | required | `<team>.cloudflareaccess.com`, in lowercase, without a scheme. |
| `audience` | required | The Access application's AUD tag. |
| `owner_subject` | required | The privately verified owner `sub` value. |
| `public_host` | required | The public MCP hostname, in lowercase, with at least one dot and at most 253 characters, without a scheme or port. |
| `port` | `8000` | The loopback listener port, 1–65535. |
| `allowed_origins` | `[]` | Exact `Origin` values: `http` or `https`, a host, and an optional port; no path (not even `/`), query, fragment, credentials, or wildcard. |
| `write_proposals` | `false` | A boolean. `true` serves the write tools; it is valid only in `vault` mode. |

`root` follows the [same rules as
`KNOWLEDGE_ROOT`](tool-contract.md#configuration-and-common-policy). The
values of `team_domain`, `audience`, `owner_subject`, and `public_host`, and
each entry in `allowed_origins`, must be 1–1024 characters long, must not
start or end with whitespace, and must not contain spaces, ASCII control
characters, or DEL. An unknown field or a missing or invalid value stops
startup with a fixed diagnostic.

## Launch modes

`mode` has no default, and no other field or environment value selects a
mode. Both modes serve the four read tools under the same gate, bounds, and
logging; they differ in the startup check of `root` and in whether the write
tools can be enabled.

- **`synthetic`**: the root must pass the [synthetic
  guard](#synthetic-guard). Use it for service checks and troubleshooting
  with the invented notes. It has no inbox: `write_proposals = true` is a
  configuration error.
- **`vault`**: the real-vault mode, for the [exposed
  scope](design-decisions.md#exposed-scope). Startup fails with
  `writable-root` if the process can write to the root directory, which stops
  a launch outside the read-only service unit. Only this mode may serve real
  notes, only for a scope that the vault owner has authorized, and no
  copy-ready example enables it. With `write_proposals = true`, startup also
  fails with `inbox` unless `<root>/inbox` is a real directory, not a
  symlink, that the process can write to. The `writable-root` check still
  applies, so the root itself stays read-only. The service creates
  `inbox/.base/` when it first needs it.

## Synthetic guard

Before serving, the launcher compares the root with
`src/knowledge_server/adapter/synthetic-vault.json`, a packaged manifest that
lists every invented note in `tests/fixtures/vault/` with its SHA-256 digest.
Startup fails on a changed, missing, or extra file, a symlink, a special
file, an entry that cannot be read, more than 100 entries, or a file larger
than 1 MiB.

The guard checks the contents at startup only, and the tools read the live
directory afterwards. Keep a synthetic root dedicated to the invented notes
for as long as it is in use.

## Listener and request gate

Uvicorn listens only on `127.0.0.1` at the configured port, with proxy-header
interpretation, access logs, and the `Server` header disabled. Lifespan
handling is required: the gate forwards lifespan events to the SDK, and a
lifespan startup that fails or raises stops the launcher.

The gate checks every HTTP request on every path and method in this order.
Nothing reaches the SDK until all checks pass.

1. Exactly one `Host` header whose value, compared case-insensitively, is
   `public_host`, `public_host:443`, `127.0.0.1`, `localhost`,
   `127.0.0.1:<port>`, or `localhost:<port>`. Otherwise: 421.
2. No `Origin` header, or exactly one whose value exactly matches an entry in
   `allowed_origins`. An empty value is rejected. Otherwise: 403.
3. Exactly one `Cf-Access-Jwt-Assertion` header that passes [assertion
   validation](#assertion-validation). Otherwise: 401.
4. Fewer than 8 authorized requests already in progress. Otherwise: 503,
   at once and without queuing.
5. A request body of at most 1 MiB, received completely within 10 seconds.
   The gate counts the bytes as they arrive and stops reading at the limit;
   it does not trust `Content-Length`. Over the limit: 413. Too slow: 408.
   If the client disconnects first, the gate records 400 and the client
   receives nothing.

Only a request that passes the Host and Origin checks and has exactly one
assertion header can cause a key fetch. The gate passes Host to the SDK in
lowercase, and the same Host and Origin lists configure the SDK's own
transport-security checks. Non-HTTP connections, such as WebSocket, are
closed. Rejection and error bodies are fixed text. After authorization,
unknown paths, including OAuth discovery paths, return 404; discovery stays
at the edge.

Only authorized requests take one of the 8 places and have their body read,
so unauthenticated requests cannot take capacity from the vault owner. The
SDK receives the complete body and never buffers more than the limit. The
bounds cannot stop a filesystem call that a worker thread has already
started: the [tool limits](tool-contract.md#initial-limits) bound that
work, and search keeps its own deadline.

Reasons for the values: a read request is a few KiB, a write request at the
[write-text limit](tool-contract.md#initial-limits) fits unless the client
escapes most of its characters, and 1 MiB leaves room for client metadata. A
body crosses loopback in milliseconds, so 10 seconds still frees a stalled
request's place. 8 places exceed what one vault owner uses and keep slow
searches within a small VM's capacity.

Every HTTP response that the ASGI application produces, including
rejections, errors, and 404s, carries `Cache-Control: no-store`, which
replaces any other `Cache-Control` value. Uvicorn's own protocol-level 400
responses and fallback 500 responses are outside this guarantee; their
bodies contain fixed text, not tool data.

## Assertion validation

The origin authorizes a request only if its `Cf-Access-Jwt-Assertion` header
passes every check below; otherwise it rejects the request before MCP
handling. The rules follow Cloudflare's [JWT validation
guide](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/).
The opaque `Authorization` token and unsigned identity headers never
authenticate a request. PyJWT verifies the token; the project adds no custom
cryptography.

- **Form.** The assertion is at most 16 KiB of ASCII and a well-formed JWT.
- **Algorithm and key.** Only RS256, with a `kid` of 1–256 characters that
  selects an RSA key of 2048–8192 bits from the trusted key set. Keys or URLs
  in the token (`jku`, `x5u`, `jwk`) are never trusted.
- **Claims.** `iss` equals `https://<team_domain>`; `aud` equals the
  configured AUD tag or, as an array, includes it; `exp` is present; `type`
  is `"app"`; `sub` exactly equals `owner_subject`; and there is no
  `common_name` claim, which marks a service credential. The email claim is
  not checked.
- **Time.** `exp`, `nbf`, and `iat`, when present, are integer Unix
  timestamps, not booleans, and are checked with 30 seconds of clock skew.

Signing keys are retrieved within these bounds:

- **Source.** The only trusted key URL is
  `https://<team_domain>/cdn-cgi/access/certs`. Fetches follow no redirects
  and ignore proxy environment variables.
- **Fetch limits.** Each fetch has a five-second total deadline. A response
  may have at most 64 KiB, and a key set 1–16 entries.
- **Cache.** Keys from a successful refresh stay fresh for one hour, much
  shorter than Cloudflare's rotation period. Each successful refresh replaces
  the whole set.
- **Refresh.** A cold cache, an expired cache, or an unknown `kid` can request
  a refresh. Refreshes are serialized, and at most one fetch attempt starts
  every 30 seconds, failures included, which bounds attacker-driven fetches.
  Unknown keys and expired caches fail closed; a failed refresh keeps other
  still-fresh keys. A request for a fresh known key does not wait for an
  in-flight refresh.
- **Key-set validation.** A refresh fails and keeps the current keys if any
  entry is not an object, contains private key material, or repeats another
  entry's string `kid`; if an eligible RSA entry cannot be parsed or is
  outside the size bounds; or if no usable key remains. Other public entries
  are skipped: those without a string `kid` of 1–256 characters, and keys that
  are not RSA signing keys for RS256.

Tests inject the key fetch and the clock, and never use live keys or network
services.

## Owner identity

The pinned owner identifier is the assertion's `sub` claim, which Cloudflare's
[application-token
documentation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/application-token/)
defines as the user identifier within the account. Removing and re-adding the
user in Cloudflare can change it; then establish it again as below.

Establish the identifier from a verified assertion, through Cloudflare's
identity administration or a one-off local procedure, never through a server
feature. An assertion inspected this way must first pass the signature,
issuer, audience, and expiry checks. Raw tokens and claims are never printed,
logged, persisted, given to agents, or committed; only the identifier itself
is stored, in private runtime configuration. Then confirm that ChatGPT's
requests succeed with it.

The HTTP server has no debug, bootstrap, or claim-inspection mode, and no
anonymous mode: the launcher refuses to start without an owner identifier.

## Logging

The HTTP process writes only three kinds of lines to stderr: a startup
category, a diagnostic event category, and one line per request.

```text
knowledge-server-http startup category=<category>
knowledge-server-http event category=<category>
knowledge-server-http request method=<method> status=<status> latency_ms=<milliseconds>
```

Startup categories, each followed by exit status 1:

| Category | Cause |
|---|---|
| `configuration` | `--config` is missing, the file is unreadable or invalid, or the root fails the synthetic guard. |
| `writable-root` | In `vault` mode, the process can write to the root directory. |
| `inbox` | In `vault` mode with `write_proposals = true`, `<root>/inbox` is missing, is not a real directory, or is not writable by the process. |
| `missing-ripgrep` | `rg` is not on `PATH`. |
| `runtime` | The server could not start, for example because the port is in use, or it failed unexpectedly. |

Event categories, each written before the line of the request it concerns:

| Category | Meaning | Client result |
|---|---|---|
| `key-fetch-failed` | A signing-key refresh failed: the fetch failed or timed out, or the key set was invalid. | 401 |
| `assertion-rejected` | The single assertion header failed validation, for any reason, including a failed key refresh. | 401 |
| `tool-failed` | A tool raised an unexpected exception. | HTTP 200 with an `INTERNAL_ERROR` tool error |

A request without an assertion header, such as a local probe, receives 401
without an event. The request bounds need no events, because their statuses
(503, 413, 408) appear in the request line. Methods outside the standard HTTP
set are recorded as `OTHER`.

One filtered handler replaces the process's log handlers and drops every
other record, including SDK, HTTPX2, Uvicorn, and other tool adapter
diagnostics, exception text, and tracebacks. The tool adapter's
unexpected-failure record becomes the `tool-failed` event without its tool
name, exception type, or location. Uvicorn access logs are disabled. Logs
therefore omit tokens, raw headers, claims, identity values or hashes, IP
addresses, paths, query strings, request bodies, tool arguments including
queries, results, note content, and exception messages. Stdio keeps its own
logging setup. Sentinel tests check these restrictions.
