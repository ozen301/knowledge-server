# Web access plan

Status: agreed design, decided 2026-10-02. **Not implemented**; the server
currently runs only over stdio. The [implementation
plan](implementation-plan.md#web-access-route) explains why this route was
chosen, and [Tasks 7–9](implementation-tasks.md) implement it.

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
cloudflared (at first on the same Ubuntu VM and network namespace)
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
administration network and is not part of this route. The first trial runs
on the Ubuntu VM; permanent hosting is on Unraid.

## Trust boundary and data handling

TLS ends at the Cloudflare edge, and the tunnel encrypts traffic between
Cloudflare and cloudflared. At first, the last hop is plain HTTP on loopback,
with cloudflared and the server in the same network namespace on the VM.
Cloudflare therefore handles decrypted requests and responses, including
queries and note excerpts, and keeps its own operational logs. ChatGPT sends
tool results to OpenAI's models, as local hosts send them to their providers.
The application's [log policy](#logging) covers only this application's logs,
not Cloudflare's or OpenAI's.

Managed OAuth is a Beta feature, and compatibility between ChatGPT, Managed
OAuth, and the SDK's HTTP transport is unproven until the Task 7 trial.
Task 9 in the [implementation tasks](implementation-tasks.md) rechecks these
dependencies and the chosen client account's data handling before the vault
owner explicitly authorizes the real vault scope. That
authorization covers these disclosed dependencies and the account choice; no
separate Beta approval is needed.

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

- Use a registration method that both sides support.
- Inspect the metadata actually served. Use the resource identifier that
  Cloudflare advertises, whether the hostname or a path, rather than forcing
  `/mcp`, and check that discovery and token requests use the same value.
- Copy any required redirect URI exactly from ChatGPT's server management
  page.
- Discovery is served by the edge; the origin serves only the MCP endpoint.
  If the trial shows that origin-served discovery is required, it needs a
  separate specification and must never expose tools without a valid
  assertion.

## Origin HTTP entry point

The entry point sits in the adapter layer beside the stdio entry point; the
core and stdio stay unchanged. It uses the SDK's Streamable HTTP transport,
preferably in stateless mode with JSON responses. The installed `mcp` 2.2.0
supports both, but whether ChatGPT works with them is unverified, so record
the negotiated protocol version. It serves the same four tools under the
[Phase 1 contract](phase-1-contract.md), with schemas and read-only
annotations semantically equal to the stdio server's. It adds no `search` or
`fetch` wrapper tools; OpenAI's [developer-mode
guide](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
does not require them.

Leave the SDK's OAuth authentication settings unconfigured. Its bearer
authentication reads the `Authorization` header, which here carries
Cloudflare's opaque token, and its settings can publish OAuth metadata that
competes with the metadata Access serves. Instead, middleware around the
whole ASGI application checks every request before MCP handling: the
[assertion validation](#assertion-validation) below, a `Host` header that
names the forwarded public hostname or a loopback name, and an `Origin`
header that is approved if present. A request without `Origin` is accepted.

The synthetic trial uses an explicit synthetic-only launch configuration and
guard, outside the core. It must not take its root from the ambient
environment, and the generic server must not hard-code a fixture path. Only
explicit Task 9 configuration activates and mounts the real vault scope.

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
7. The identity equals the pinned owner identifier. A missing or unexpected
   identity and any service credential are rejected. Whether the email claim
   must also match depends on the verified meaning of the claims; do not add
   it by default.

The opaque `Authorization` token and unsigned identity headers never
authenticate a request. Use a maintained JWT library, chosen and justified
under the repository's dependency rule before code is written, and no custom
cryptography or OAuth server. Automated tests inject synthetic keys and need
no network. Record the reasons for the chosen timeouts, skew, cache, and
refresh bounds.

## Owner identity

The pinned owner identifier must come from verified assertions, not from claim
names alone. Establish it before tool dispatch is enabled, through the
provider's identity administration or a one-off local bootstrap procedure,
not through a server debug mode or a shipped inspection feature. An assertion
inspected this way must first pass the signature, issuer, audience, and
expiry checks, and tool authorization stays deny-all meanwhile. Then confirm
that assertions from ChatGPT's Managed OAuth flow carry the pinned
identifier.

Diagnostics show only claim names and types. Raw tokens and claims are never
printed, logged, persisted, given to agents, or committed. Only the verified
owner identifier itself may be stored, by the procedure or the vault owner,
directly in private runtime configuration outside Git.

If the provider's mechanisms cannot establish the identifier safely,
provisioning stops; if no suitable stable identity binding exists, the
authentication architecture is reconsidered. The HTTP entry point refuses to
start without a configured owner identifier, and there is no anonymous or
accept-any-identity mode.

## Logging

Logs record only the method or tool name, a status or error category, and
latency. They omit tokens, raw headers, claims, identity values or hashes, IP
addresses, tool arguments including queries, results, and note content.
Sentinel tests check that secrets and content do not reach the logs.

## Shutdown and revocation

Stopping cloudflared or the origin is the local shutdown. Before real-vault
use, test disabling the tunnel's public-hostname route from Cloudflare while
Access protection stays in place, and record the configuration behavior, the
effect on active connections, and the observed time to take effect. If this
route cannot be disabled reliably, choose and verify another supported
routing shutdown. Do not count DNS deletion or credential rotation as
immediate shutdown without proof.

Never disable or delete Access protection as a kill switch, because removing
the gate does not stop routing. A dedicated, tested deny-all policy may add
protection, but do not assume it acts at once. Managed OAuth reevaluates
policy when a token is refreshed, so a policy change may not revoke issued
tokens immediately; measure and record the actual behavior before real-vault
use.

## Runtime isolation

On Unraid, the server and cloudflared run in an isolated container network
with no published origin port on the host, while cloudflared keeps the
outbound access it needs. The server runs as a non-root process with a
read-only mount of a dedicated local vault checkout or read-only materialized
snapshot; synchronization stays outside the MCP process. Secrets and
configuration stay outside Git. Request, rate, and concurrency limits get
values chosen and justified during implementation.

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

The provider statements above were checked against the linked pages on
2026-10-02. Live compatibility is unproven. Recheck the pages before Task 9.
