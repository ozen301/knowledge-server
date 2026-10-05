# Web access plan

Status: agreed design, decided 2026-10-02; local HTTP contract updated
2026-10-04; live-trial findings recorded and planned runtime updated
2026-10-05. The protected loopback HTTP entry point and its synthetic-only
launcher are implemented beside stdio, and the synthetic ChatGPT trial
through this route succeeded. The real-vault mode, the request bounds, and
the permanent VM services described in [runtime isolation](#runtime-isolation)
are planned for Task 8 and not implemented. The [implementation
plan](implementation-plan.md#web-access-route) explains why this route was
chosen, and [Tasks 7–9](implementation-tasks.md) track its progress.

## Route

```text
ChatGPT
    | HTTPS to https://mcp.<domain>/mcp with an opaque OAuth access token
    v
Cloudflare edge
    - terminates TLS
    - Access application with Managed OAuth and an owner-only policy
    - forwards the request with a signed Cf-Access-Jwt-Assertion header
    | Cloudflare Tunnel: encrypted connection opened outbound by cloudflared
    v
cloudflared (in the same Ubuntu VM as the server)
    | plain HTTP on loopback
    v
knowledge-server HTTP entry point (adapter layer)
    - checks the assertion, Host, and Origin before any MCP handling
    - MCP Streamable HTTP, four tools -> knowledge core -> vault
```

ChatGPT is the first web client, tested first with a personal ChatGPT Plus
account. A lab or workspace account would need its own account and
data-governance check before the real vault is exposed. `<domain>` must be a
domain whose DNS Cloudflare manages, in the account that holds the Zero Trust
organization, as [Managed
OAuth](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/)
requires; the domain, hostname, and identity provider for the owner's login
are provisioning choices. Because cloudflared connects outbound, the router
needs no inbound port forwarding. Tailscale remains the private
administration network and is not part of this route. The synthetic trial ran
inside the Ubuntu VM, which is also the target for permanent services. Unraid
hosts the VM and requires no application-specific configuration.

## Trust boundary and data handling

TLS ends at the Cloudflare edge, and the tunnel encrypts traffic between
Cloudflare and cloudflared. The last hop is plain HTTP on loopback, with
cloudflared and the server running in the same VM, both in the trial and in
the planned permanent setup.
Cloudflare therefore handles decrypted requests and responses, including
queries and note excerpts, and keeps its own operational logs. ChatGPT sends
tool results to OpenAI's models, as local hosts send them to their providers.
The application's [log policy](#logging) covers only this application's logs,
not Cloudflare's or OpenAI's.

Managed OAuth was documented as a Beta feature when this design was agreed.
The Task 7 synthetic trial confirmed compatibility between ChatGPT, Managed
OAuth, and the SDK's HTTP transport for the tested configuration. Task 9 in
the [implementation tasks](implementation-tasks.md) rechecks these
dependencies and the chosen client account's data handling before the vault
owner explicitly authorizes the exact real vault scope. That authorization
covers these disclosed dependencies and the account choice; no separate Beta
approval is needed.

## Edge configuration

- A dedicated self-hosted Access application, with Managed OAuth enabled,
  covers the whole MCP hostname. No other service shares the hostname.
- An allow policy admits only the vault owner's identity, with no bypass for
  tool routes.
- The tunnel routes only the MCP hostname to the loopback origin and returns
  404 for unmatched requests.
- No Cloudflare caching rule overrides the origin's `Cache-Control: no-store`.
- If requests fail, inspect Cloudflare's challenge and security events, and
  change only a setting shown to be incompatible, as narrowly as the service
  plan allows. Do not disable protections in bulk or assume that every feature
  can be excluded per host; if the plan cannot scope a change, reassess.

## Discovery and registration

According to [OpenAI's authentication
guide](https://developers.openai.com/plugins/build/auth), ChatGPT supports
client ID metadata documents (CIMD), dynamic client registration (DCR), and
predefined clients, requires PKCE with `S256`, and sends the `resource` value
from the protected resource metadata exactly as advertised. Managed OAuth
supports RFC 8707 resource indicators. Cloudflare documents that Access
answers unauthenticated non-browser requests with a 401 challenge and serves
the discovery metadata, so discovery stays reachable without login.

- DCR is the validated registration method. In the synthetic trial, ChatGPT
  selected it automatically; Cloudflare did not advertise CIMD. Revisit CIMD
  only if the provider advertises support and there is a concrete
  operational reason to change.
- Inspect the metadata actually served. Use the resource identifier that
  Cloudflare advertises, whether the hostname or a path, rather than forcing
  `/mcp`, and check that discovery and token requests use the same value.
- Check the redirect URI shown in ChatGPT's server management page against
  the provider's allowlist. According to OpenAI's [redirect
  guidance](https://developers.openai.com/plugins/build/auth#redirect-url),
  its form depends on the authorization server's issuer-identification
  support. The trial settings below record what worked; they are not a
  universal callback configuration.
- Discovery is served by the edge; the origin serves only the MCP endpoint.
  If a future integration requires origin-served discovery, it needs a
  separate specification and must never expose tools without a valid
  assertion.

### OAuth settings validated in the synthetic trial

The vault owner's trial report records these values. The URLs are
illustrative: `mcp.example.com` and `example` replace the personal hostname
and team name, and the tested structure is preserved.

| Item | Observed value |
|---|---|
| MCP endpoint and advertised resource | `https://mcp.example.com/mcp` |
| Authorization server (issuer) | `https://example.cloudflareaccess.com` |
| Protected-resource metadata, named by the 401 `WWW-Authenticate` challenge | `https://mcp.example.com/.well-known/cloudflare-access-protected-resource/mcp` |
| Authorization-server discovery | `https://mcp.example.com/.well-known/oauth-authorization-server` |

Discovery advertised authorization code and refresh-token grants, DCR, PKCE
`S256`, and token authentication method `none` alongside client-secret
methods. No application scopes or OIDC support were advertised, and no base
scopes were added manually.

The first ChatGPT connection attempt failed with a generic settings
rejection. In this trial, adding `https://chatgpt.com/connector/oauth/*` to
Managed OAuth's redirect allowlist resolved it. Both **Allow localhost
clients** and **Allow loopback clients** remained disabled. These settings
govern OAuth callbacks, not the origin's loopback listener or its
`allowed_origins` setting. The connection worked with these settings.

The [Task 7
record](implementation-tasks.md#task-7--http-entry-point-and-synthetic-chatgpt-trial-complete)
lists the observed results and accepted evidence limitations.

## Origin HTTP entry point

The entry point sits in the adapter layer beside the stdio entry point; the
core and stdio stay unchanged. It serves the same four tools under the
[Phase 1 contract](phase-1-contract.md) over the SDK's Streamable HTTP
transport, in stateless mode with JSON responses. Their schemas and read-only
annotations are semantically equal to the stdio server's. It adds no `search`
or `fetch` wrapper tools; OpenAI's [developer-mode
guide](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
does not require them. The installed `mcp` 2.2.0 supports this mode, and
ChatGPT used it successfully in the synthetic trial.

The SDK's OAuth authentication settings stay unconfigured. Its bearer
authentication reads the `Authorization` header, which here carries
Cloudflare's opaque token, and its settings can publish OAuth metadata that
competes with the metadata Access serves. Instead, a gate around the whole
ASGI application checks every request before MCP handling: the [assertion
validation](#assertion-validation) below, a `Host` header that names the
forwarded public hostname or a loopback name, and an `Origin` header that is
approved if present. A request without `Origin` is accepted. The [local
implementation contract](#local-http-implementation-contract) gives the exact
rules.

## Assertion validation

The origin authorizes a request only if its `Cf-Access-Jwt-Assertion` header
passes all of these checks; otherwise it rejects the request without
dispatching it to MCP. Checks 2-5 follow Cloudflare's [JWT validation
guide](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/).

1. The header is present and is a well-formed JWT.
2. The algorithm is RS256. Any other algorithm is rejected.
3. The signature verifies with a key from the trusted key set, which comes
   only from the configured team certificate endpoint. The token's key ID
   (`kid`) may select a key from that set, but URLs or keys embedded in the
   token never establish trust. Keys are cached. To support Cloudflare's key
   rotation, an unknown key ID may trigger a refresh of the key set; refreshes
   are bounded and rate-limited, and each fetch has a timeout. If a permitted
   refresh finds the key, the assertion can pass; if no usable trusted key is
   available, the request is rejected.
4. The issuer equals the configured team issuer.
5. The audience contains the configured application AUD tag. A string
   audience must equal it; an array audience must include it.
6. The expiry claim is present and the assertion is not expired; time-based
   claims are checked with an explicit, bounded clock skew.
7. The `sub` claim equals the pinned [owner identifier](#owner-identity). A
   missing or different identity and any service credential are rejected.
   The email claim is not checked separately.

The opaque `Authorization` token and unsigned identity headers never
authenticate a request. Verification uses PyJWT, a maintained JWT library;
the project adds no custom cryptography or OAuth server. Automated tests
inject synthetic keys and need no network. The [assertion and key
rules](#assertion-and-key-rules) give the exact limits and their reasons.

## Owner identity

The pinned owner identifier is the assertion's `sub` claim. Cloudflare's
[application-token
documentation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/application-token/)
defines `sub` as the user identifier within the account, and [Managed
OAuth](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/managed-oauth/)
documents the signed origin assertion for its user identity. Removing and
re-adding the user in Cloudflare can change `sub`; the new value must then be
verified privately and pinned again.

The identifier must come from verified assertions, not from claim names
alone. Establish it before tool dispatch is enabled, through the provider's
identity administration or a one-off local bootstrap procedure, not through a
server debug mode or a shipped inspection feature. An assertion inspected this
way must first pass the signature, issuer, audience, and expiry checks, and
tool authorization stays deny-all meanwhile. Then confirm that assertions from
ChatGPT's Managed OAuth flow carry the pinned identifier.

Diagnostics show only claim names and types. Raw tokens and claims are never
printed, logged, persisted, given to agents, or committed. Only the verified
owner identifier itself may be stored, by the procedure or the vault owner,
directly in private runtime configuration outside Git.

For the synthetic trial, the vault owner established the identifier privately
from an authenticated Cloudflare Access identity session and stored it in
private launch configuration. The reproducible provisioning procedure stays
in the vault owner's private deployment notes. The HTTP server has no debug,
bootstrap, or claim-inspection mode. The launcher refuses to start without an
owner identifier, and there is no anonymous or accept-any-identity mode, so no
tool is reachable before the identifier is pinned.

If the provider's mechanisms cannot establish the identifier safely,
provisioning stops; if no suitable stable identity binding exists, the
authentication architecture is reconsidered.

## Local HTTP implementation contract

The Task 7 implementation follows this contract, and `tests/test_http.py`
checks it offline. The [usage
guide](usage.md#prepare-the-synthetic-http-trial) gives the launch procedure.

### Launch configuration

`knowledge-server-http --config /absolute/private/trial.toml` reads one
explicit TOML file, given by absolute path, of at most 16 KiB. It ignores
`KNOWLEDGE_ROOT`, proxy environment variables, and `.env` files.

| Field | Default | Rule |
|---|---|---|
| `mode` | required | Must be `"synthetic"`; no other mode exists. |
| `root` | required | Absolute path to an existing readable directory: the dedicated trial directory. |
| `team_domain` | required | `<team>.cloudflareaccess.com`, in lowercase, without a scheme. |
| `audience` | required | The Access application's AUD tag. |
| `owner_subject` | required | The privately verified owner `sub` value. |
| `public_host` | required | The public MCP hostname, in lowercase, with at least one dot and at most 253 characters, without a scheme or port. |
| `port` | `8000` | The loopback listener port, 1–65535. |
| `allowed_origins` | `[]` | Exact `Origin` values: `http` or `https`, a host, and an optional port; no path (not even `/`), query, fragment, credentials, or wildcard. |

`root` follows the [same rules as
`KNOWLEDGE_ROOT`](phase-1-contract.md#configuration-and-common-policy). The
values of `team_domain`, `audience`, `owner_subject`, and `public_host`, and
each entry in `allowed_origins`, must be 1–1024 characters long, must not
start or end with whitespace, and must not contain spaces, ASCII control
characters, or DEL. An unknown field or a missing or invalid value stops
startup with a fixed diagnostic.

### Synthetic trial guard

Before serving, the launcher compares the root with
`src/knowledge_server/adapter/synthetic-vault.json`, a packaged manifest that
lists every invented note in `tests/fixtures/vault/` with its SHA-256 digest.
Startup fails on a changed, missing, or extra file, a symlink, a special
file, an entry that cannot be read, more than 100 entries, or a file larger
than 1 MiB.

The guard checks the contents at startup only, and the tools read the live
directory afterwards. Keep the trial directory dedicated to the invented
notes for as long as it is in use.

The generic HTTP application receives an explicit path policy and knows no
fixture location or environment root. There is no real-vault launch mode yet.
Task 8 specifies and tests one with invented notes and keeps this guard for
the synthetic mode. Only explicit configuration selecting the real-vault mode,
used in Task 9 for an authorized scope, may activate real notes.

### Listener and request gate

Uvicorn listens only on `127.0.0.1` at the configured port, with proxy-header
interpretation, access logs, and the `Server` header disabled. Lifespan
handling is required: the gate forwards lifespan events to the SDK, and a
lifespan startup that fails or raises stops the launcher.

The MCP endpoint is `/mcp`, created with the installed MCP 2.2.0 API
`streamable_http_app(json_response=True, stateless_http=True)`. The gate,
`HTTPApplication`, checks every HTTP request on every path and method in this
order. Nothing reaches the SDK until all checks pass.

1. Exactly one `Host` header whose value, compared case-insensitively, is
   `public_host`, `public_host:443`, `127.0.0.1`, `localhost`,
   `127.0.0.1:<port>`, or `localhost:<port>`. Otherwise: 421.
2. No `Origin` header, or exactly one whose value exactly matches an entry in
   `allowed_origins`. An empty value is rejected. Otherwise: 403.
3. Exactly one `Cf-Access-Jwt-Assertion` header that passes [assertion
   validation](#assertion-validation). Otherwise: 401.

Only a request that passes the Host and Origin checks and has exactly one
assertion header can cause a key fetch. The gate passes Host to the SDK in
lowercase, and the same Host and Origin lists configure the SDK's own
transport-security checks. Non-HTTP connections, such as WebSocket, are
closed. Rejection and error bodies are fixed text. After authorization,
unknown paths, including OAuth discovery paths, return 404; discovery stays
at the edge.

Every HTTP response that the ASGI application produces, including
rejections, errors, and 404s, carries `Cache-Control: no-store`, which
replaces any other `Cache-Control` value. Uvicorn's own protocol-level 400
responses and fallback 500 responses are outside this guarantee; their
bodies contain fixed text, not tool data.

### Assertion and key rules

`PyJWT[crypto]` verifies RS256 signatures and parses JWKs. It was already
locked as an SDK dependency and is declared directly because the application
uses it. HTTPX2 (key fetching), Starlette (ASGI types and responses), Uvicorn
(the listener), and Cryptography (RSA key checks) were also already locked
and are declared directly for the same reason. Check library APIs against
the installed versions and [PyJWT's API
documentation](https://pyjwt.readthedocs.io/en/stable/api.html).

The assertion must contain `iss`, `aud`, `exp`, `sub`, and `type`, with
`type = "app"`, `sub` exactly equal to `owner_subject`, and no `common_name`
claim, which marks a service credential. The time claims `exp`, `nbf`, and
`iat`, when present, must be integer Unix timestamps; booleans are rejected.
PyJWT verifies the signature, issuer, audience, expiry, not-before, and
issued-at with 30 seconds of clock skew: enough for modest clock differences
while keeping the expiry grace bounded. Only RS256 is accepted, with a `kid`
of 1–256 characters and an RSA key of 2048–8192 bits. Assertions longer than
16 KiB or containing non-ASCII characters are rejected before parsing.

Signing keys are retrieved within these bounds:

- **Source.** The issuer is `https://<team_domain>`, and the only trusted key
  URL is `<issuer>/cdn-cgi/access/certs`. Fetches follow no redirects and
  ignore proxy environment variables. Token `jku`, `x5u`, and `jwk` fields are
  never trusted.
- **Fetch limits.** Each fetch, including an injected one, has a five-second
  total deadline. A response may have at most 64 KiB, and a key set 1–16
  entries. These limits fit a small rotating key set without allowing
  unbounded network work.
- **Cache.** Keys from a successful refresh stay fresh for one hour, much
  shorter than Cloudflare's documented rotation period. Each successful
  refresh replaces the whole set.
- **Refresh.** Refreshes are serialized, and at most one fetch attempt starts
  every 30 seconds, failures included, to bound attacker-driven unknown-key
  requests. A cold cache, an expired cache, or an unknown `kid` can request a
  refresh within this bound. Unknown keys and expired caches fail closed; a
  failed refresh keeps other still-fresh keys. A request for a fresh known key
  does not wait for an in-flight refresh.
- **Key-set validation.** A refresh fails and keeps the current keys if any
  entry is not an object, contains private key material, or repeats another
  entry's string `kid`; if an eligible RSA entry cannot be parsed or is
  outside the size bounds; or if no usable key remains. These checks cover
  every entry. Other public entries are skipped: those without a string `kid`
  of 1–256 characters, and keys that are not RSA signing keys for RS256.

Tests inject the fetch function and the monotonic clock, and never use live
keys or network services.

### Logging

The HTTP process writes only two kinds of lines to stderr: a fixed startup
category, and one line per request.

```text
knowledge-server-http startup category=<category>
knowledge-server-http request method=<method> status=<status> latency_ms=<milliseconds>
```

Methods outside the standard HTTP set are recorded as `OTHER`. One filtered
handler replaces the process's log handlers and drops every other record,
including SDK, HTTPX2, Uvicorn, and tool adapter diagnostics, exception text,
and tracebacks. Uvicorn access logs are disabled. Logs therefore omit tokens,
raw headers, claims, identity values or hashes, IP addresses, paths, query
strings, request bodies, tool arguments including queries, results, and note
content. Stdio keeps its own logging setup. Sentinel tests check these
restrictions. Task 8 adds fixed diagnostic categories under the same
restrictions, as [diagnostics and health](#diagnostics-and-health) describes.

## Shutdown and revocation

Stopping cloudflared or the origin is the local shutdown. Both were tested
independently in the synthetic trial: ChatGPT lost access, then recovered
after the stopped process restarted. A local stop works only while its
management access, such as access to the host, is available.

Before real-vault use, verify one usable emergency-stop procedure on the
target runtime, and state the management access it depends on. A
Cloudflare-side route shutdown is optional only if the vault owner accepts
that dependency; the owner has not accepted it yet. Otherwise, also test
disabling the tunnel's public-hostname route from Cloudflare while Access
protection stays in place, and record the configuration behavior, the effect
on active connections, and the observed time to take effect. If this route
cannot be disabled reliably, choose and verify another supported routing
shutdown. Do not count DNS deletion or credential rotation as immediate
shutdown without proof.

Never disable or delete Access protection as a kill switch, because removing
the gate does not stop routing. A policy change alone is not a verified stop.
A dedicated, tested deny-all policy may add protection, but do not assume it
acts at once. Managed OAuth reevaluates policy when a token is refreshed, so a
policy change may not revoke issued tokens immediately. Measuring that delay
is optional; without a measurement, assume that issued tokens stay valid until
they expire.

## Runtime isolation

Status: planned for Task 8 and not implemented. The current launcher serves
the invented notes only.

The service has one user, the vault owner, who maintains it by hand;
availability is best effort. These assumptions keep the runtime simple. They
do not relax the assertion, path, key-cache, or logging protections above.

### VM services and network

The server and cloudflared run as ordinary services in the same Ubuntu VM,
communicating over loopback. The origin listens only on `127.0.0.1`. Use the
VM's service manager for startup at boot, stop, and restart. No containers or
Unraid-specific deployment configuration are needed.

The server runs as a non-root service account without write permission to the
exposed notes. Configuration and secrets stay outside Git. A dedicated local
vault checkout is synchronized outside the MCP process. Choose filesystem
permissions that let the service read the notes while keeping synchronization
under the vault owner's control.

Both cloudflared and the origin need outbound access: cloudflared for the
tunnel, and the origin to fetch signing keys from the [trusted key
URL](#assertion-and-key-rules). The service configuration sets the Host that
cloudflared forwards explicitly, to a value that the gate accepts.

### Exposed scope

The root is explicit configuration. It can be the whole checkout or one
subtree, with read-only access for the server; tool paths and citations are
relative to that root. A selection spread across several directories needs
its own design before dependent work. The exact scope must be known and
authorized before activation, but preparing the services does not need it.

Task 8 adds an explicit real-vault launch mode, specified and tested with
invented notes. Only explicit configuration selects it, and the synthetic mode
and its [guard](#synthetic-trial-guard) remain.

### Request bounds

The gate counts request-body bytes as they arrive and rejects a body over a
fixed limit before the SDK buffers it; a `Content-Length` check alone does not
bound a body. Reading the body has a deadline, and a small global bound limits
concurrent requests. Task 8 chooses the values and records the reasons. These
bounds cannot stop a filesystem call that a worker thread has already started:
the [Phase 1 limits](phase-1-contract.md#initial-limits) bound that work, and
search keeps its own deadline. Rate limiting is deferred until a need is
demonstrated.

### Diagnostics and health

The [log policy](#logging) gains fixed, content-free categories. They
include one for failed signing-key retrieval, after which the gate rejects
the request with 401, and one for internal tool failures, which the client
receives as tool errors inside HTTP 200 responses.

A local check expects 401 for an unauthenticated request to `/mcp`. That 401
shows only that the listener and gate respond; it does not show that key
retrieval, authorization, or MCP handling work. Document service status and
restart commands in the runbook. No separate health-monitoring system is
required.

### Edge cache

Task 8 reviews the Cloudflare cache configuration for Uvicorn's own 400 and
500 responses, which the `no-store` guarantee does not cover. The live
behavior stays unobserved until Task 9.

## Contingency

WorkOS AuthKit is a fallback only if a demonstrated compatibility or identity
limitation of Managed OAuth remains after configuration and debugging;
account permissions or a blocked request do not prove one. It is not an
implementation dependency, and switching to it changes the trust boundary, so
it needs a new design review.

## Out of scope

A generic authentication framework or custom OAuth server; native citation
cards, company knowledge, and deep research integration; retrieval upgrades;
MCP portals or server aggregation; and NAS redesign.

## Provider documentation

Provider statements were checked against the linked pages on 2026-10-02.
The application-token, Managed OAuth, and JWT API documentation was rechecked
for local implementation on 2026-10-04. OpenAI's authentication guide was
rechecked on 2026-10-05 for registration and redirect guidance. The live
results are vault-owner-reported evidence, separate from provider claims.
Recheck the provider pages before Task 9.
