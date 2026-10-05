"""Serve the four knowledge tools over HTTP behind an authorization gate.

The MCP SDK's Streamable HTTP transport handles MCP at `/mcp`. Before it sees
a request, the gate checks the `Host` and `Origin` headers and the Cloudflare
Access assertion: a signed JSON Web Token (JWT) that Cloudflare Access adds
in the `Cf-Access-Jwt-Assertion` header to each request it lets through. It
then bounds the number of authorized requests in progress and reads each
request body within a size and time limit.
"""

import asyncio
import logging
import time

from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from knowledge_server.adapter.http_auth import AssertionVerifier, CachedKeys, KeyFetch
from knowledge_server.adapter.http_config import HTTPConfig
from knowledge_server.adapter.http_logging import EVENT_FORMAT, REQUEST_FORMAT
from knowledge_server.adapter.server import create_server
from knowledge_server.core.paths import PathPolicy

MAX_BODY_BYTES = 1024 * 1024
BODY_TIMEOUT = 10.0
MAX_REQUESTS = 8

_logger = logging.getLogger("knowledge_server.http")
_METHODS = {
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "HEAD",
    "OPTIONS",
    "CONNECT",
    "TRACE",
}


class HTTPApplication:
    """Check each HTTP request before the wrapped MCP application sees it.

    A request passes only with an allowed `Host`, an allowed or absent
    `Origin`, and one valid Cloudflare Access assertion for the configured
    owner. Every path and method is checked. An authorized request then
    needs a free place among `max_requests` and a complete body of at most
    `max_body` bytes, received within `body_timeout` seconds; the wrapped
    application receives that body in one message.

    Attributes:
        app: The wrapped ASGI application, which also receives lifespan events.
        config: The allowed Host and Origin values and the owner settings.
        verifier: Checks the assertion; any error counts as a rejection.
        max_body: The largest accepted request body, in bytes.
        body_timeout: Seconds allowed for receiving the whole body.
        max_requests: The number of authorized requests that may be in
            progress at once.
    """

    def __init__(
        self,
        app: ASGIApp,
        config: HTTPConfig,
        verifier: AssertionVerifier,
        *,
        max_body: int = MAX_BODY_BYTES,
        body_timeout: float = BODY_TIMEOUT,
        max_requests: int = MAX_REQUESTS,
    ) -> None:
        """Wrap an application with whole-request authorization and bounds.

        Args:
            app: The downstream ASGI callable.
            config: Validated HTTP settings.
            verifier: The trusted owner assertion verifier.
            max_body: The body size limit; tests use a small value.
            body_timeout: The body deadline; tests use a short value.
            max_requests: The concurrency bound; tests use a small value.
        """
        self.app = app
        self.config = config
        self.verifier = verifier
        self.max_body = max_body
        self.body_timeout = body_timeout
        self.max_requests = max_requests
        self._active = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle one ASGI connection.

        Lifespan events, the server's startup and shutdown messages, go to the
        wrapped application unchanged. Other non-HTTP connections, such as
        WebSocket, are closed. An HTTP request that fails a check or a bound
        receives a fixed rejection. If the wrapped application raises before
        it responds, the request receives a fixed 500 response. Every
        response sent through this method carries `Cache-Control: no-store`,
        which asks caches not to store it. Each request logs only its method,
        status, and latency.

        Args:
            scope: The ASGI connection metadata.
            receive: The ASGI incoming message callable.
            send: The ASGI outgoing message callable.
        """
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        started = False
        status = 500
        began = time.monotonic()

        async def no_store(message: Message) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                started = True
                status = message["status"]
                message = {
                    **message,
                    "headers": [
                        (name, value)
                        for name, value in message.get("headers", [])
                        if name.lower() != b"cache-control"
                    ]
                    + [(b"cache-control", b"no-store")],
                }
            await send(message)

        try:
            rejection = await self._check(scope)
            if rejection is None and self._active >= self.max_requests:
                rejection = 503
            if rejection is not None:
                await Response("Request rejected.", status_code=rejection)(
                    scope, receive, no_store
                )
            else:
                # No await separates the check above from this increment, so
                # concurrent requests on the event loop cannot both take the
                # last place.
                self._active += 1
                try:
                    await self._forward(scope, receive, no_store)
                finally:
                    self._active -= 1
        except Exception:  # noqa: BLE001
            if not started:
                await Response("Request failed.", status_code=500)(
                    scope, receive, no_store
                )
        finally:
            method = scope.get("method", "")
            _logger.info(
                REQUEST_FORMAT,
                method if method in _METHODS else "OTHER",
                status,
                (time.monotonic() - began) * 1000,
            )

    async def _forward(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Read the bounded body, then pass the request to the wrapped app.

        The body is read before the wrapped application starts, so it never
        buffers more than `max_body` bytes. After the body, `receive` passes
        the client's later messages, such as a disconnect, through.
        """
        body = bytearray()
        try:
            async with asyncio.timeout(self.body_timeout):
                while True:
                    message = await receive()
                    if message["type"] != "http.request":
                        rejection = 400
                        break
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > self.max_body:
                        rejection = 413
                        break
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        rejection = None
                        break
        except TimeoutError:
            rejection = 408
        if rejection is not None:
            await Response("Request rejected.", status_code=rejection)(
                scope, receive, send
            )
            return
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": bytes(body), "more_body": False}

        # Lowercase Host because the SDK's own Host check is case-sensitive.
        safe_scope = dict(scope)
        safe_scope["headers"] = [
            (name, value.lower() if name.lower() == b"host" else value)
            for name, value in scope.get("headers", [])
        ]
        await self.app(safe_scope, replay, send)

    async def _check(self, scope: Scope) -> int | None:
        """Return a rejection status, or None if the request may proceed.

        Host, Origin, and the number of assertion headers are checked first,
        so only a request that passes them can cause a key fetch.

        Args:
            scope: The ASGI HTTP connection metadata.

        Returns:
            421 unless there is exactly one allowed `Host`, compared without
            regard to case; 403 if `Origin` is present but repeated or not
            allowed; 401 unless there is exactly one valid assertion;
            otherwise None. An absent `Origin` passes its check. A present
            but invalid single assertion logs the `assertion-rejected` event.
        """
        headers = scope.get("headers", [])
        hosts = [value for name, value in headers if name.lower() == b"host"]
        if (
            len(hosts) != 1
            or hosts[0].decode("latin-1").lower() not in self.config.allowed_hosts
        ):
            return 421
        origins = [value for name, value in headers if name.lower() == b"origin"]
        if origins and (
            len(origins) != 1
            or origins[0].decode("latin-1") not in self.config.allowed_origins
        ):
            return 403
        assertions = [
            value
            for name, value in headers
            if name.lower() == b"cf-access-jwt-assertion"
        ]
        if len(assertions) != 1:
            return 401
        if not await self.verifier.authorize(assertions[0].decode("latin-1")):
            _logger.warning(EVENT_FORMAT, "assertion-rejected")
            return 401
        return None


def create_http_app(
    policy: PathPolicy,
    config: HTTPConfig,
    *,
    ripgrep: str,
    key_fetch: KeyFetch | None = None,
) -> HTTPApplication:
    """Build the gated MCP HTTP application for the four knowledge tools.

    The SDK application serves MCP at `/mcp` in stateless mode, keeping no
    session between requests, and answers with JSON instead of an event
    stream. Its own Host and Origin checks use the same allowed values as the
    gate.

    Args:
        policy: The path policy for the served directory; the application
            reads no root from the environment.
        config: Validated HTTP and owner settings.
        ripgrep: The ripgrep executable used by knowledge search.
        key_fetch: Replaces the HTTPS signing-key fetch, for offline tests;
            None fetches from `config.jwks_url`.

    Returns:
        The gate wrapping the SDK application. Run it with lifespan support,
        because the SDK prepares its request handling at lifespan startup.
    """
    server = create_server(policy, ripgrep=ripgrep)
    sdk_app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=list(config.allowed_hosts),
            allowed_origins=list(config.allowed_origins),
        ),
        host="127.0.0.1",
    )
    return HTTPApplication(
        sdk_app,
        config,
        AssertionVerifier(config, CachedKeys(config, fetch=key_fetch)),
    )
