"""Offline security, transport, bound, and launch tests for HTTP access."""

import asyncio
import io
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from mcp import Client
from mcp_types import CallToolResult, TextContent, Tool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from knowledge_server.adapter.http import HTTPApplication, create_http_app
from knowledge_server.adapter.http_auth import (
    AssertionVerifier,
    CachedKeys,
    JWKSFetcher,
)
from knowledge_server.adapter.http_config import HTTPConfig, load_launch_config
from knowledge_server.adapter.http_logging import configure_http_logging
from knowledge_server.adapter.server import create_server
from knowledge_server.adapter.synthetic import verify_synthetic_root
from knowledge_server.config import ConfigurationError
from knowledge_server.core.paths import PathPolicy

FIXTURES = Path(__file__).parent / "fixtures" / "vault"
_FOUND_RG = shutil.which("rg")
if _FOUND_RG is None:
    raise RuntimeError("ripgrep is required for HTTP tests")
RG: str = _FOUND_RG
SENTINEL = "PRIVATE-HTTP-SENTINEL"


@pytest.fixture(scope="module")
def signing_keys() -> tuple[Any, Any]:
    """Create two invented RSA keys: the trusted key and an untrusted or new key."""
    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
    )


@pytest.fixture
def config() -> HTTPConfig:
    """Use invented audience and owner values and one allowed origin."""
    return HTTPConfig(
        team_domain="example.cloudflareaccess.com",
        audience="synthetic-audience",
        owner_subject="synthetic-owner",
        public_host="mcp.example.com",
        allowed_origins=("https://mcp.example.com",),
    )


def _jwk(key: Any, kid: str = "key-a") -> dict[str, Any]:
    return {
        **json.loads(RSAAlgorithm.to_jwk(key.public_key())),
        "kid": kid,
        "alg": "RS256",
        "use": "sig",
    }


def _claims(config: HTTPConfig) -> dict[str, Any]:
    return {
        "iss": config.issuer,
        "aud": config.audience,
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
        "sub": config.owner_subject,
        "type": "app",
    }


def _token(
    key: Any,
    config: HTTPConfig,
    changes: dict[str, Any] | None = None,
    *,
    remove: tuple[str, ...] = (),
    kid: str = "key-a",
    headers: dict[str, Any] | None = None,
) -> str:
    claims = _claims(config) | (changes or {})
    for name in remove:
        claims.pop(name, None)
    return jwt.encode(
        claims, key, algorithm="RS256", headers={"kid": kid} | (headers or {})
    )


def _run(coro: Any) -> Any:
    return asyncio.run(asyncio.wait_for(coro, timeout=20))


def _keys(config: HTTPConfig, signing_keys: tuple[Any, Any]) -> CachedKeys:
    async def fetch(url: str) -> dict[str, Any]:
        assert url == config.jwks_url
        return {"keys": [_jwk(signing_keys[0])]}

    return CachedKeys(config, fetch=fetch)


@pytest.mark.parametrize(
    "changes,remove",
    [
        ({}, ("exp",)),
        ({}, ("iss",)),
        ({}, ("aud",)),
        ({}, ("sub",)),
        ({}, ("type",)),
        ({"exp": int(time.time()) - 120}, ()),
        ({"nbf": int(time.time()) + 120}, ()),
        ({"iat": int(time.time()) + 120}, ()),
        ({"iss": "https://attacker.invalid"}, ()),
        ({"aud": "other"}, ()),
        ({"aud": ["other"]}, ()),
        ({"aud": ["synthetic-audience", 1]}, ()),
        ({"sub": "another-owner"}, ()),
        ({"sub": ""}, ()),
        ({"sub": None}, ()),
        ({"common_name": "service-credential"}, ()),
        ({"common_name": None}, ()),
        ({"type": "service"}, ()),
        ({"exp": "9999999999"}, ()),
        ({"exp": True}, ()),
        ({"exp": 9999999999.5}, ()),
        ({"nbf": "0"}, ()),
        ({"iat": False}, ()),
    ],
)
def test_invalid_claims_fail_closed(
    config: HTTPConfig,
    signing_keys: tuple[Any, Any],
    changes: dict[str, Any],
    remove: tuple[str, ...],
) -> None:
    """A trusted signature cannot pass a missing, wrong, or mistyped claim."""
    verifier = AssertionVerifier(config, _keys(config, signing_keys))
    assert (
        _run(
            verifier.authorize(_token(signing_keys[0], config, changes, remove=remove))
        )
        is False
    )


def test_signature_algorithm_and_malformed_assertions(
    config: HTTPConfig, signing_keys: tuple[Any, Any]
) -> None:
    """Unsigned, forged, symmetric, malformed, and oversized tokens fail."""
    verifier = AssertionVerifier(config, _keys(config, signing_keys))
    tokens = [
        "",
        "broken",
        "a.b.c",
        "x" * 16385,
        _token(signing_keys[1], config),
        jwt.encode(
            _claims(config),
            "synthetic-secret-which-is-at-least-32-bytes",
            algorithm="HS256",
            headers={"kid": "key-a"},
        ),
        jwt.encode(_claims(config), "", algorithm="none", headers={"kid": "key-a"}),
        jwt.encode(_claims(config), signing_keys[0], algorithm="RS256"),
        _token(signing_keys[0], config, kid="x" * 257),
    ]

    async def check() -> None:
        for token in tokens:
            assert await verifier.authorize(token) is False

    _run(check())


@pytest.mark.parametrize(
    "audience", ["synthetic-audience", ["other", "synthetic-audience"]]
)
def test_owner_and_bounded_clock_skew(
    config: HTTPConfig, signing_keys: tuple[Any, Any], audience: Any
) -> None:
    """An owner token passes with a string or list `aud` and small clock skew."""
    verifier = AssertionVerifier(config, _keys(config, signing_keys))
    assert (
        _run(
            verifier.authorize(
                _token(
                    signing_keys[0],
                    config,
                    {
                        "aud": audience,
                        "exp": int(time.time()) - 5,
                        "nbf": int(time.time()) + 5,
                    },
                )
            )
        )
        is True
    )


def test_embedded_token_keys_never_establish_trust(
    config: HTTPConfig, signing_keys: tuple[Any, Any]
) -> None:
    """Keys and key URLs in the token header (`jku`, `x5u`, `jwk`) are ignored."""
    verifier = AssertionVerifier(config, _keys(config, signing_keys))
    headers = {
        "jku": "https://attacker.invalid/keys",
        "x5u": "https://attacker.invalid/cert",
        "jwk": _jwk(signing_keys[1]),
    }

    async def check() -> None:
        assert (
            await verifier.authorize(_token(signing_keys[1], config, headers=headers))
            is False
        )
        assert (
            await verifier.authorize(_token(signing_keys[0], config, headers=headers))
            is True
        )

    _run(check())


def test_rotation_cache_and_global_refresh_bounds(
    config: HTTPConfig, signing_keys: tuple[Any, Any]
) -> None:
    """Rotation refreshes once after cooldown; concurrent unknown IDs stay bounded."""
    now = [0.0]
    calls: list[str] = []

    async def fetch(url: str) -> dict[str, Any]:
        calls.append(url)
        await asyncio.sleep(0)
        keys = [_jwk(signing_keys[0])]
        if len(calls) > 1:
            keys.append(_jwk(signing_keys[1], "key-b"))
        return {"keys": keys}

    async def check() -> None:
        cache = CachedKeys(config, fetch=fetch, clock=lambda: now[0])
        assert await cache.get("key-a") is not None
        assert await cache.get("key-a") is not None
        assert await cache.get("key-b") is None
        assert len(calls) == 1
        now[0] = 31
        assert await cache.get("key-b") is not None
        assert await AssertionVerifier(config, cache).authorize(
            _token(signing_keys[1], config, kid="key-b")
        )
        now[0] = 62
        assert (
            await asyncio.gather(*(cache.get(f"unknown-{i}") for i in range(30)))
            == [None] * 30
        )
        assert len(calls) == 3
        assert all(url == config.jwks_url for url in calls)

    _run(check())


def test_outage_backoff_and_expired_keys(
    config: HTTPConfig, signing_keys: tuple[Any, Any]
) -> None:
    """Failed refreshes preserve fresh keys but cannot extend cache lifetime."""
    now = [0.0]
    calls = [0]

    async def fetch(url: str) -> dict[str, Any]:
        calls[0] += 1
        if calls[0] == 1:
            return {"keys": [_jwk(signing_keys[0])]}
        raise TimeoutError(SENTINEL)

    async def check() -> None:
        cache = CachedKeys(config, fetch=fetch, clock=lambda: now[0])
        assert await cache.get("key-a") is not None
        now[0] = 31
        assert await cache.get("unknown") is None
        assert await cache.get("key-a") is not None
        assert await cache.get("another") is None
        assert calls[0] == 2
        now[0] = 3601
        assert await cache.get("key-a") is None
        assert await cache.get("key-a") is None
        assert calls[0] == 3

    _run(check())


def test_cold_outage_is_rate_limited(config: HTTPConfig) -> None:
    """An unavailable key source never opens the gate or retries every request."""
    calls = [0]

    async def fetch(url: str) -> dict[str, Any]:
        calls[0] += 1
        raise OSError(SENTINEL)

    async def check() -> None:
        cache = CachedKeys(config, fetch=fetch)
        assert (
            await asyncio.gather(*(cache.get("missing") for _ in range(20)))
            == [None] * 20
        )
        assert calls[0] == 1

    _run(check())


@pytest.mark.parametrize(
    "kind",
    ["duplicate", "empty", "too-many", "weak", "private", "wrong-use", "wrong-alg"],
)
def test_invalid_trusted_key_sets_rejected(
    config: HTTPConfig, signing_keys: tuple[Any, Any], kind: str
) -> None:
    """Malformed or unsuitable trusted key sets cannot validate assertions."""
    key = _jwk(signing_keys[0])
    if kind == "weak":
        key = _jwk(rsa.generate_private_key(public_exponent=65537, key_size=1024))
    elif kind == "private":
        key = json.loads(RSAAlgorithm.to_jwk(signing_keys[0])) | {
            "kid": "key-a",
            "alg": "RS256",
        }
    elif kind == "wrong-use":
        key["use"] = "enc"
    elif kind == "wrong-alg":
        key["alg"] = "HS256"
    keys = [key]
    if kind == "duplicate":
        keys *= 2
    elif kind == "empty":
        keys = []
    elif kind == "too-many":
        keys = [key | {"kid": str(i)} for i in range(17)]

    async def fetch(url: str) -> dict[str, Any]:
        return {"keys": keys}

    assert _run(CachedKeys(config, fetch=fetch).get("key-a")) is None


@pytest.mark.parametrize(
    "kind", ["redirect", "oversize", "malformed", "timeout", "status"]
)
def test_bounded_key_fetch(config: HTTPConfig, kind: str) -> None:
    """The HTTPS fetcher rejects redirects, errors, bad bodies, and timeouts."""
    requests: list[str] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(str(request.url))
        if kind == "timeout":
            raise httpx2.ReadTimeout(SENTINEL)
        if kind == "redirect":
            return httpx2.Response(
                302, headers={"location": "https://attacker.invalid"}
            )
        if kind == "status":
            return httpx2.Response(503, text=SENTINEL)
        return httpx2.Response(
            200, content=b"x" * 65537 if kind == "oversize" else b"not-json"
        )

    fetcher = JWKSFetcher(transport=httpx2.MockTransport(handler))

    async def check() -> None:
        with pytest.raises((ValueError, OSError, TimeoutError, httpx2.HTTPError)):
            await fetcher(config.jwks_url)
        assert requests == [config.jwks_url]

    _run(check())


def test_injected_fetch_total_deadline(
    config: HTTPConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cache also bounds a fetch implementation that never completes."""
    import knowledge_server.adapter.http_auth as auth_module

    monkeypatch.setattr(auth_module, "FETCH_TIMEOUT", 0.02)

    async def fetch(url: str) -> dict[str, Any]:
        await asyncio.Event().wait()
        return {"keys": []}

    assert _run(CachedKeys(config, fetch=fetch).get("key-a")) is None


async def _request(
    app: HTTPApplication,
    headers: list[tuple[bytes, bytes]],
    *,
    method: str = "POST",
    path: str = "/mcp",
) -> list[Message]:
    messages: list[Message] = []
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": SENTINEL.encode(),
        "headers": headers,
        "client": ("192.0.2.99", 1234),
        "server": ("127.0.0.1", 8000),
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": SENTINEL.encode()}

    async def send(message: Message) -> None:
        messages.append(message)

    await app(scope, receive, send)
    return messages


@pytest.mark.parametrize(
    "case,status",
    [
        ("missing", 401),
        ("opaque", 401),
        ("unsigned", 401),
        ("duplicate-token", 401),
        ("bad-host", 421),
        ("missing-host", 421),
        ("duplicate-host", 421),
        ("bad-origin", 403),
        ("empty-origin", 403),
        ("duplicate-origin", 403),
        ("valid", 204),
        ("no-origin", 204),
    ],
)
def test_whole_app_gate_before_dispatch(
    config: HTTPConfig, signing_keys: tuple[Any, Any], case: str, status: int
) -> None:
    """On any path and method, only fully valid headers reach the application.

    Each response, including a rejection, carries `Cache-Control: no-store`.
    """
    calls: list[str] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(scope["path"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = HTTPApplication(
        downstream, config, AssertionVerifier(config, _keys(config, signing_keys))
    )
    headers = [
        (b"host", b"mcp.example.com"),
        (b"cf-access-jwt-assertion", _token(signing_keys[0], config).encode()),
    ]
    if case in {"missing", "opaque", "unsigned"}:
        headers.pop()
        if case == "opaque":
            headers.append((b"authorization", SENTINEL.encode()))
        if case == "unsigned":
            headers.extend(
                [
                    (b"cf-access-authenticated-user-email", b"owner@example.invalid"),
                    (b"cf-access-user-id", config.owner_subject.encode()),
                ]
            )
    elif case == "duplicate-token":
        headers.append(headers[-1])
    elif case == "bad-host":
        headers[0] = (b"host", SENTINEL.encode())
    elif case == "missing-host":
        headers.pop(0)
    elif case == "duplicate-host":
        headers.append(headers[0])
    elif "origin" in case and case != "no-origin":
        value = (
            b"https://mcp.example.com"
            if case == "duplicate-origin"
            else (b"" if case == "empty-origin" else SENTINEL.encode())
        )
        headers.append((b"origin", value))
        if case == "duplicate-origin":
            headers.append((b"origin", value))
    elif case == "valid":
        headers.append((b"origin", b"https://mcp.example.com"))

    async def check() -> None:
        for method in ("POST", "GET", "DELETE", "OPTIONS"):
            messages = await _request(app, headers, method=method, path="/unexpected")
            assert messages[0]["status"] == status
            assert (b"cache-control", b"no-store") in messages[0]["headers"]
        assert len(calls) == (4 if status == 204 else 0)

    _run(check())


@pytest.mark.parametrize(
    "host",
    [
        "mcp.example.com",
        "MCP.EXAMPLE.COM",
        "mcp.example.com:443",
        "localhost",
        "localhost:8000",
        "127.0.0.1",
        "127.0.0.1:8000",
    ],
)
def test_allowed_hosts(
    config: HTTPConfig, signing_keys: tuple[Any, Any], host: str
) -> None:
    """Only the public hostname and the configured loopback names are allowed."""

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    app = HTTPApplication(
        downstream, config, AssertionVerifier(config, _keys(config, signing_keys))
    )
    messages = _run(
        _request(
            app,
            [
                (b"host", host.encode()),
                (b"cf-access-jwt-assertion", _token(signing_keys[0], config).encode()),
            ],
        )
    )
    assert messages[0]["status"] == 204


@asynccontextmanager
async def _lifespan(app: ASGIApp) -> AsyncIterator[None]:
    """Run an application's lifespan startup and shutdown around the body.

    This drives the same messages as Uvicorn. Each event fails if the
    application reports failure, ends early, or takes more than five seconds.

    Args:
        app: The ASGI application whose lifespan is exercised.

    Yields:
        None, while the application is started.
    """
    incoming: asyncio.Queue[Message] = asyncio.Queue()
    outgoing: asyncio.Queue[Message] = asyncio.Queue()
    scope: Scope = {
        "type": "lifespan",
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "state": {},
    }

    async def run_app() -> None:
        await app(scope, incoming.get, outgoing.put)

    app_task = asyncio.create_task(run_app())

    async def exchange(event: str, expected: str) -> None:
        await incoming.put({"type": event})
        response = asyncio.create_task(outgoing.get())
        try:
            done, _ = await asyncio.wait(
                {app_task, response}, timeout=5, return_when=asyncio.FIRST_COMPLETED
            )
            assert done, "Lifespan event timed out"
            if response in done:
                assert response.result()["type"] == expected
            else:
                await app_task
                raise AssertionError("Application ended before completing lifespan")
        finally:
            response.cancel()
            await asyncio.gather(response, return_exceptions=True)

    try:
        await exchange("lifespan.startup", "lifespan.startup.complete")
        try:
            yield
        finally:
            await exchange("lifespan.shutdown", "lifespan.shutdown.complete")
        await app_task
    finally:
        if not app_task.done():
            app_task.cancel()
        await asyncio.gather(app_task, return_exceptions=True)


@pytest.fixture
def http_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[io.StringIO]:
    """Capture the HTTP log output and restore process logging afterward."""
    import knowledge_server.adapter.http_logging as logs

    output = io.StringIO()
    root_logger = logging.getLogger()
    handlers, level = root_logger.handlers[:], root_logger.level
    snapshots = {
        name: (logger.level, logger.propagate, logger.handlers[:])
        for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    monkeypatch.setattr(logs.sys, "stderr", output)
    configure_http_logging()
    try:
        yield output
    finally:
        root_logger.handlers = handlers
        root_logger.setLevel(level)
        for name, state in snapshots.items():
            logger = logging.getLogger(name)
            logger.setLevel(state[0])
            logger.propagate = state[1]
            logger.handlers = state[2]


@pytest.mark.parametrize("host", ["mcp.example.com", "MCP.EXAMPLE.COM"])
def test_http_stdio_schemas_and_results(
    config: HTTPConfig,
    signing_keys: tuple[Any, Any],
    tmp_path: Path,
    http_logs: io.StringIO,
    host: str,
) -> None:
    """Stateless HTTP initializes and serves the same four schemas and results.

    The application runs inside its lifespan, and a public Host in any letter
    case passes both the gate's and the SDK's Host checks.
    """
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text(f"# Note\n{SENTINEL}\n", encoding="utf-8")
    policy = PathPolicy(root)

    async def fetch(url: str) -> dict[str, Any]:
        return {"keys": [_jwk(signing_keys[0])]}

    calls = [
        ("knowledge_search", {"query": SENTINEL}),
        ("knowledge_read", {"path": "note.md"}),
        ("knowledge_list", {}),
        ("knowledge_info", {"path": "note.md"}),
        ("knowledge_read", {"path": "../outside.md"}),
        ("knowledge_read", {"path": "note.md", "start_line": "1"}),
        (SENTINEL, {"private": SENTINEL}),
    ]

    async def check() -> None:
        async with Client(create_server(policy, ripgrep=RG)) as stdio:
            stdio_tools = [
                tool.model_dump(mode="json")
                for tool in (await stdio.list_tools()).tools
            ]
            stdio_results = [
                (await stdio.call_tool(name, args)).model_dump(
                    mode="json", include={"content", "structured_content", "is_error"}
                )
                for name, args in calls
            ]
        app = create_http_app(policy, config, ripgrep=RG, key_fetch=fetch)
        async with (
            _lifespan(app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://127.0.0.1:8000",
                headers={
                    "host": host,
                    "cf-access-jwt-assertion": _token(signing_keys[0], config),
                    "accept": "application/json, text/event-stream",
                },
            ) as client,
        ):

            async def rpc(
                method: str, params: dict[str, Any], number: int
            ) -> dict[str, Any]:
                response = await client.post(
                    "/mcp",
                    json={
                        "jsonrpc": "2.0",
                        "id": number,
                        "method": method,
                        "params": params,
                    },
                )
                assert response.status_code == 200, response.text
                assert response.headers["cache-control"] == "no-store"
                assert response.headers["content-type"].startswith("application/json")
                assert "mcp-session-id" not in response.headers
                return response.json()["result"]

            initialized = await rpc(
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "offline-test", "version": "1"},
                },
                1,
            )
            assert initialized["protocolVersion"] == "2025-11-25"
            discovered = await rpc("tools/list", {}, 2)
            assert [
                Tool.model_validate(tool).model_dump(mode="json")
                for tool in discovered["tools"]
            ] == stdio_tools
            for number, ((name, args), expected) in enumerate(
                zip(calls, stdio_results, strict=True), 3
            ):
                assert (
                    CallToolResult.model_validate(
                        await rpc(
                            "tools/call", {"name": name, "arguments": args}, number
                        )
                    ).model_dump(
                        mode="json",
                        include={"content", "structured_content", "is_error"},
                    )
                    == expected
                )
            for path in ("/unknown", "/.well-known/oauth-protected-resource"):
                response = await client.get(path)
                assert response.status_code == 404
                assert response.headers["cache-control"] == "no-store"

    _run(check())
    output = http_logs.getvalue()
    assert "status=200" in output
    for private in (SENTINEL, config.owner_subject, "note.md", "eyJ", "Traceback"):
        assert private not in output


@pytest.mark.parametrize(
    "changes",
    [
        {"owner_subject": ""},
        {"audience": ""},
        {"team_domain": "attacker.invalid"},
        {"team_domain": "example.cloudflareaccess.com/path"},
        {"public_host": "https://mcp.example.com"},
        {"public_host": "mcp.example.com:443"},
        {"port": 0},
        {"allowed_origins": ("*",)},
        {"allowed_origins": ("https://host/path",)},
    ],
)
def test_invalid_http_configuration(
    config: HTTPConfig, changes: dict[str, Any]
) -> None:
    """Invalid owner, audience, team domain, host, port, or origin values fail."""
    with pytest.raises(ConfigurationError):
        replace(config, **changes)


def _config_text(root: Path, mode: str = "synthetic") -> str:
    return f'mode = "{mode}"\nroot = {json.dumps(str(root))}\nteam_domain = "example.cloudflareaccess.com"\naudience = "synthetic-audience"\nowner_subject = "synthetic-owner"\npublic_host = "mcp.example.com"\n'


def test_explicit_launch_configuration_and_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The TOML selects the mode and root, even with `KNOWLEDGE_ROOT` set.

    Only the two named modes load; a missing mode, a missing field, or an
    unknown field fails.
    """
    root = tmp_path / "invented"
    shutil.copytree(FIXTURES, root)
    monkeypatch.setenv("KNOWLEDGE_ROOT", str(tmp_path / "real-vault-do-not-open"))
    path = tmp_path / "config.toml"
    for mode in ("synthetic", "vault"):
        path.write_text(_config_text(root, mode), encoding="utf-8")
        launch = load_launch_config(path)
        assert (launch.mode, launch.root) == (mode, root)
    verify_synthetic_root(launch.root)
    for text in (
        _config_text(root, "real"),
        _config_text(root, "VAULT"),
        _config_text(root).replace('mode = "synthetic"\n', ""),
        _config_text(root).replace('mode = "synthetic"', "mode = true"),
        _config_text(root).replace('owner_subject = "synthetic-owner"', ""),
        _config_text(root).replace(f"root = {json.dumps(str(root))}", ""),
        _config_text(root) + 'unknown = "value"\n',
    ):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_launch_config(path)


def test_write_proposals_setting(tmp_path: Path) -> None:
    """Only `vault` mode can enable the write tools, with a boolean."""
    root = tmp_path / "invented"
    shutil.copytree(FIXTURES, root)
    path = tmp_path / "config.toml"
    path.write_text(_config_text(root, "vault"), encoding="utf-8")
    assert load_launch_config(path).write_proposals is False
    path.write_text(
        _config_text(root, "vault") + "write_proposals = true\n", encoding="utf-8"
    )
    assert load_launch_config(path).write_proposals is True
    for text in (
        _config_text(root, "synthetic") + "write_proposals = true\n",
        _config_text(root, "vault") + 'write_proposals = "true"\n',
        _config_text(root, "vault") + "write_proposals = 1\n",
    ):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_launch_config(path)


@pytest.mark.parametrize("change", ["modify", "extra", "missing", "symlink", "fifo"])
def test_synthetic_guard_rejects_changed_scope(tmp_path: Path, change: str) -> None:
    """The startup check rejects changed, extra, or missing notes, links, and FIFOs."""
    import os

    root = tmp_path / "invented"
    shutil.copytree(FIXTURES, root)
    note = root / "infrastructure" / "nas-configuration.md"
    if change == "modify":
        note.write_text(SENTINEL, encoding="utf-8")
    elif change == "extra":
        (root / "extra.md").write_text(SENTINEL, encoding="utf-8")
    elif change == "missing":
        note.unlink()
    elif change == "symlink":
        note.unlink()
        note.symlink_to(FIXTURES / "infrastructure" / "nas-configuration.md")
    else:
        os.mkfifo(root / "pipe")
    with pytest.raises(ConfigurationError):
        verify_synthetic_root(root)


def test_trial_cli_missing_configuration() -> None:
    """The executable fails safely without its configuration file."""
    result = subprocess.run(
        [sys.executable, "-m", "knowledge_server.adapter.http_main"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "configuration" in result.stderr.lower()
    assert "Traceback" not in result.stderr


def test_http_logging_excludes_sentinels(
    config: HTTPConfig, signing_keys: tuple[Any, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dependency logs and a failing request leave no secrets or tracebacks."""
    import knowledge_server.adapter.http_logging as logs

    output = io.StringIO()
    root_logger = logging.getLogger()
    previous = root_logger.handlers[:]
    previous_level = root_logger.level
    snapshots = {
        name: (logger.level, logger.propagate, logger.handlers[:], logger.filters[:])
        for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    monkeypatch.setattr(logs.sys, "stderr", output)
    try:
        configure_http_logging()
        for name in (
            "mcp.server.transport_security",
            "mcp.server.streamable_http",
            "httpx2",
            "uvicorn.error",
            "knowledge_server.adapter.server",
        ):
            logging.getLogger(name).critical(SENTINEL, exc_info=ValueError(SENTINEL))

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            raise RuntimeError(SENTINEL)

        app = HTTPApplication(
            downstream, config, AssertionVerifier(config, _keys(config, signing_keys))
        )
        token = _token(signing_keys[0], config)
        messages = _run(
            _request(
                app,
                [
                    (b"host", b"mcp.example.com"),
                    (b"authorization", SENTINEL.encode()),
                    (b"cf-access-jwt-assertion", token.encode()),
                ],
                method=SENTINEL,
            )
        )
        assert messages[0]["status"] == 500
        text = output.getvalue()
        assert SENTINEL not in text
        assert token not in text
        assert config.owner_subject not in text
        assert "OTHER" in text and "500" in text
        assert "Traceback" not in text
    finally:
        root_logger.handlers = previous
        root_logger.setLevel(previous_level)
        for name, state in snapshots.items():
            logger = logging.getLogger(name)
            logger.setLevel(state[0])
            logger.propagate = state[1]
            logger.handlers = state[2]
            logger.filters = state[3]


@pytest.mark.parametrize(
    "stop_signal,mode",
    [
        (signal.SIGTERM, "synthetic"),
        (signal.SIGINT, "synthetic"),
        (signal.SIGTERM, "vault"),
    ],
)
def test_loopback_process_rejects_without_network_keys(
    tmp_path: Path, stop_signal: signal.Signals, mode: str
) -> None:
    """A real launch binds only loopback, rejects probes safely, and stops cleanly.

    The vault mode serves invented notes that are not the sample set from a
    root that the process cannot write to.
    """
    import socket

    root = tmp_path / "invented"
    if mode == "synthetic":
        shutil.copytree(FIXTURES, root)
    else:
        if os.geteuid() == 0:
            pytest.skip("root can write to any directory")
        root.mkdir()
        (root / "invented.md").write_text(f"# {SENTINEL}\n", encoding="utf-8")
        root.chmod(0o555)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    configuration = tmp_path / "config.toml"
    configuration.write_text(
        _config_text(root, mode) + f"port = {port}\n", encoding="utf-8"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "knowledge_server.adapter.http_main",
            "--config",
            str(configuration),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        with httpx2.Client(trust_env=False, timeout=0.5) as client:
            while True:
                assert process.poll() is None, (
                    "HTTP launch exited before accepting a probe"
                )
                try:
                    response = client.get(f"http://127.0.0.1:{port}/mcp?{SENTINEL}")
                    break
                except httpx2.ConnectError:
                    if time.monotonic() >= deadline:
                        pytest.fail("HTTP launch did not become ready")
                    time.sleep(0.05)
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
            assert SENTINEL not in response.text
            response = client.get(
                f"http://127.0.0.1:{port}/mcp",
                headers={
                    "host": SENTINEL,
                    "x-forwarded-for": "192.0.2.99",
                    "origin": SENTINEL,
                },
            )
            assert response.status_code == 421
        # Linux exposes sockets in this process's network namespace. Restrict
        # the assertion to the randomly selected port belonging to this test.
        addresses = []
        for line in Path(f"/proc/{process.pid}/net/tcp").read_text().splitlines()[1:]:
            columns = line.split()
            address, hex_port = columns[1].split(":")
            if int(hex_port, 16) == port and columns[3] == "0A":
                addresses.append(address)
        assert addresses == ["0100007F"]
    finally:
        process.send_signal(stop_signal)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
        root.chmod(0o755)
    # After its graceful shutdown, Uvicorn re-raises the captured signal.
    # uvicorn.run absorbs the resulting KeyboardInterrupt for SIGINT, while
    # SIGTERM ends the process.
    if stop_signal == signal.SIGINT:
        assert process.returncode == 0
        assert "KeyboardInterrupt" not in stderr
    else:
        assert process.returncode in (0, -signal.SIGTERM)
    assert all(
        line.startswith(
            ("knowledge-server-http request ", "knowledge-server-http startup ")
        )
        for line in stderr.splitlines()
    )
    assert stdout == ""
    assert "status=401" in stderr and "status=421" in stderr
    for private in (SENTINEL, str(root), str(configuration), "192.0.2.99", "Traceback"):
        assert private not in stderr


def test_http_bind_failure_has_safe_diagnostic(tmp_path: Path) -> None:
    """An occupied loopback port exits with a fixed category and no raw errors."""
    import socket

    configuration = tmp_path / "private.toml"
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        configuration.write_text(
            _config_text(FIXTURES.resolve()) + f"port = {port}\n", encoding="utf-8"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "knowledge_server.adapter.http_main",
                "--config",
                str(configuration),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "knowledge-server-http startup category=runtime\n"


def test_fresh_keys_do_not_wait_for_refresh(
    config: HTTPConfig, signing_keys: tuple[Any, Any]
) -> None:
    """A slow refresh cannot delay requests that already have a fresh key."""
    now = [0.0]
    calls = [0]

    async def check() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def fetch(url: str) -> dict[str, Any]:
            calls[0] += 1
            if calls[0] > 1:
                started.set()
                await release.wait()
            return {"keys": [_jwk(signing_keys[0])]}

        cache = CachedKeys(config, fetch=fetch, clock=lambda: now[0])
        assert await cache.get("key-a") is not None
        now[0] = 31
        refresh = asyncio.create_task(cache.get("unknown"))
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            assert await asyncio.wait_for(cache.get("key-a"), timeout=1) is not None
            assert not refresh.done()
        finally:
            release.set()
            await refresh
        assert refresh.result() is None
        assert calls[0] == 2
        now[0] = 3632
        assert await cache.get("key-a") is not None
        assert calls[0] == 3

    _run(check())


@pytest.mark.parametrize("kind", ["ec", "encryption", "algorithm", "missing-kid"])
def test_mixed_public_key_sets(
    config: HTTPConfig, signing_keys: tuple[Any, Any], kind: str
) -> None:
    """Unsupported public entries cannot invalidate a usable signing key."""
    other = _jwk(signing_keys[1], "skipped")
    if kind == "ec":
        other = json.loads(
            ECAlgorithm.to_jwk(ec.generate_private_key(ec.SECP256R1()).public_key())
        ) | {"kid": "skipped", "alg": "ES256", "use": "sig"}
    elif kind == "encryption":
        other["use"] = "enc"
    elif kind == "algorithm":
        other["alg"] = "RS512"
    else:
        other.pop("kid")

    async def fetch(url: str) -> dict[str, Any]:
        return {"keys": [other, _jwk(signing_keys[0])]}

    async def check() -> None:
        cache = CachedKeys(config, fetch=fetch)
        verifier = AssertionVerifier(config, cache)
        assert await verifier.authorize(_token(signing_keys[0], config))
        assert await cache.get("skipped") is None
        assert not await verifier.authorize(
            _token(signing_keys[1], config, kid="skipped")
        )

    _run(check())


@pytest.mark.parametrize("kind", ["duplicate", "private", "oversized", "malformed-rsa"])
def test_mixed_sets_still_fail_closed(
    config: HTTPConfig, signing_keys: tuple[Any, Any], kind: str
) -> None:
    """Set-wide checks cover skipped entries; an unusable eligible key fails."""
    skipped = _jwk(signing_keys[1], "other") | {"use": "enc"}
    entries = [_jwk(signing_keys[0]), skipped]
    if kind == "duplicate":
        skipped["kid"] = "key-a"
    elif kind == "private":
        skipped["k"] = "invented-private-material"
    elif kind == "oversized":
        entries += [skipped | {"kid": f"extra-{index}"} for index in range(15)]
    else:
        entries.append({"kty": "RSA", "kid": "broken", "n": "invalid", "e": "invalid"})

    async def fetch(url: str) -> dict[str, Any]:
        return {"keys": entries}

    assert _run(CachedKeys(config, fetch=fetch).get("key-a")) is None


@pytest.mark.parametrize("case", ["host", "origin", "missing", "duplicate"])
def test_header_rejection_never_fetches_keys(
    config: HTTPConfig, signing_keys: tuple[Any, Any], case: str
) -> None:
    """Invalid header structure is rejected before any trusted-source fetch."""
    calls: list[str] = []

    async def fetch(url: str) -> dict[str, Any]:
        calls.append(url)
        raise AssertionError("Rejected requests must not fetch keys")

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        raise AssertionError("Rejected requests must not dispatch")

    # A well-formed header with an unknown key would request a refresh if the
    # Host/Origin/header-count checks did not precede assertion handling.
    token = _token(signing_keys[0], config, kid="unknown")
    headers = [
        (b"host", b"mcp.example.com"),
        (b"cf-access-jwt-assertion", token.encode()),
    ]
    if case == "host":
        headers[0] = (b"host", b"invalid.example")
    elif case == "origin":
        headers.append((b"origin", b"https://invalid.example"))
    elif case == "missing":
        headers.pop()
    else:
        headers.append(headers[-1])
    app = HTTPApplication(
        downstream, config, AssertionVerifier(config, CachedKeys(config, fetch=fetch))
    )
    messages = _run(_request(app, headers))
    assert (
        messages[0]["status"]
        == {"host": 421, "origin": 403, "missing": 401, "duplicate": 401}[case]
    )
    assert calls == []


def test_failed_lifespan_stops_launch_safely(tmp_path: Path) -> None:
    """A raising lifespan stops startup and suppresses its exception details.

    Uvicorn's default "auto" mode would treat this exception as missing
    lifespan support and keep serving; the launcher requires "on".
    """
    import socket

    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    configuration = tmp_path / "config.toml"
    configuration.write_text(
        _config_text(FIXTURES.resolve()) + f"port = {port}\n", encoding="utf-8"
    )
    script = """
import sys
from pathlib import Path
from knowledge_server.adapter import http_main
async def broken(scope, receive, send):
    if scope["type"] == "lifespan":
        raise RuntimeError("PRIVATE-LIFESPAN-SENTINEL")
    raise RuntimeError("Unexpected HTTP dispatch")
http_main.create_http_app = lambda *args, **kwargs: broken
raise SystemExit(http_main.main(["--config", sys.argv[1]]))
"""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, str(configuration)],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("Unsupported lifespan was ignored; HTTP launch kept running")
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "knowledge-server-http startup category=runtime\n"


def test_vault_mode_refuses_writable_root(tmp_path: Path) -> None:
    """The real-vault mode does not serve a root that the process can write to.

    The root holds the sample notes, so only the writability check can stop
    this launch.
    """
    root = tmp_path / "invented"
    shutil.copytree(FIXTURES, root)
    configuration = tmp_path / "config.toml"
    configuration.write_text(_config_text(root, "vault"), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "knowledge_server.adapter.http_main",
            "--config",
            str(configuration),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "knowledge-server-http startup category=writable-root\n"


def _gate(
    config: HTTPConfig,
    signing_keys: tuple[Any, Any],
    downstream: ASGIApp,
    **bounds: Any,
) -> HTTPApplication:
    return HTTPApplication(
        downstream,
        config,
        AssertionVerifier(config, _keys(config, signing_keys)),
        **bounds,
    )


async def _exchange(
    app: HTTPApplication,
    headers: list[tuple[bytes, bytes]],
    receive: Receive,
) -> list[Message]:
    messages: list[Message] = []
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 8000),
    }

    async def send(message: Message) -> None:
        messages.append(message)

    await app(scope, receive, send)
    return messages


def _authorized(config: HTTPConfig, signing_keys: tuple[Any, Any]) -> list[Any]:
    return [
        (b"host", b"mcp.example.com"),
        (b"cf-access-jwt-assertion", _token(signing_keys[0], config).encode()),
    ]


def _chunks(*parts: bytes) -> Receive:
    """Deliver body parts in order, then wait as a connected client does."""
    pending = list(parts)

    async def receive() -> Message:
        if pending:
            body = pending.pop(0)
            return {"type": "http.request", "body": body, "more_body": bool(pending)}
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    return receive


async def _echo(scope: Scope, receive: Receive, send: Send) -> None:
    body = bytearray()
    while True:
        message = await receive()
        body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": bytes(body)})


@pytest.mark.parametrize(
    "parts,status",
    [
        ((b"a" * 6, b"b" * 4), 200),
        ((b"a" * 6, b"b" * 5), 413),
        ((b"a" * 11,), 413),
        ((b"",), 200),
    ],
)
def test_body_bytes_are_counted_as_they_arrive(
    config: HTTPConfig,
    signing_keys: tuple[Any, Any],
    parts: tuple[bytes, ...],
    status: int,
) -> None:
    """A body over the limit is refused before the application sees it.

    A small `Content-Length` does not change the count, and a body within the
    limit reaches the application complete.
    """
    calls: list[bytes] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(b"called")
        await _echo(scope, receive, send)

    app = _gate(config, signing_keys, downstream, max_body=10)
    headers = _authorized(config, signing_keys) + [(b"content-length", b"1")]
    messages = _run(_exchange(app, headers, _chunks(*parts)))
    assert messages[0]["status"] == status
    assert (b"cache-control", b"no-store") in messages[0]["headers"]
    if status == 200:
        assert messages[1]["body"] == b"".join(parts)
    else:
        assert calls == []
        assert messages[1]["body"] == b"Request rejected."


@pytest.mark.parametrize("case,status", [("stalled", 408), ("disconnect", 400)])
def test_incomplete_body_is_refused(
    config: HTTPConfig, signing_keys: tuple[Any, Any], case: str, status: int
) -> None:
    """A stalled body times out with 408; a disconnected client gets 400."""

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        raise AssertionError("An incomplete body must not dispatch")

    sent = [False]

    async def receive() -> Message:
        if not sent[0]:
            sent[0] = True
            return {"type": "http.request", "body": b"{", "more_body": True}
        if case == "disconnect":
            return {"type": "http.disconnect"}
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    app = _gate(config, signing_keys, downstream, body_timeout=0.05)
    messages = _run(_exchange(app, _authorized(config, signing_keys), receive))
    assert messages[0]["status"] == status


def test_trickling_body_meets_a_total_deadline(
    config: HTTPConfig, signing_keys: tuple[Any, Any]
) -> None:
    """A body that keeps arriving within the size limit still times out.

    Each chunk arrives well within the deadline, but the whole body does not.
    """
    calls: list[None] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(None)

    async def receive() -> Message:
        await asyncio.sleep(0.01)
        return {"type": "http.request", "body": b"x", "more_body": True}

    app = _gate(config, signing_keys, downstream, body_timeout=0.1)
    messages = _run(_exchange(app, _authorized(config, signing_keys), receive))
    assert messages[0]["status"] == 408
    assert (b"cache-control", b"no-store") in messages[0]["headers"]
    assert calls == []


def test_cancelled_request_frees_its_place(
    config: HTTPConfig, signing_keys: tuple[Any, Any]
) -> None:
    """A request cancelled while it holds the only place gives it back."""
    entered = asyncio.Event()
    calls: list[None] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        calls.append(None)
        if len(calls) == 1:
            entered.set()
            await asyncio.Event().wait()
        await _echo(scope, receive, send)

    async def check() -> None:
        app = _gate(config, signing_keys, downstream, max_requests=1)
        authorized = _authorized(config, signing_keys)
        held = asyncio.create_task(_exchange(app, authorized, _chunks(b"{}")))
        await entered.wait()
        held.cancel()
        with pytest.raises(asyncio.CancelledError):
            await held
        after = await _exchange(app, authorized, _chunks(b"{}"))
        assert after[0]["status"] == 200

    _run(check())


def test_unauthorized_bodies_are_not_read(
    config: HTTPConfig, signing_keys: tuple[Any, Any]
) -> None:
    """A rejected request is answered without reading its body."""

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        raise AssertionError("Rejected requests must not dispatch")

    async def receive() -> Message:
        raise AssertionError("Rejected requests must not read their body")

    app = _gate(config, signing_keys, downstream)
    messages = _run(_exchange(app, [(b"host", b"mcp.example.com")], receive))
    assert messages[0]["status"] == 401


def test_concurrent_authorized_requests_are_bounded(
    config: HTTPConfig, signing_keys: tuple[Any, Any]
) -> None:
    """Requests over the bound get 503 at once; places free up after each one.

    Unauthorized requests neither take a place nor receive 503.
    """
    release = asyncio.Event()
    entered: list[None] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        entered.append(None)
        await release.wait()
        await _echo(scope, receive, send)

    async def check() -> None:
        app = _gate(config, signing_keys, downstream, max_requests=2)
        authorized = _authorized(config, signing_keys)
        held = [
            asyncio.create_task(_exchange(app, authorized, _chunks(b"{}")))
            for _ in range(2)
        ]
        while len(entered) < 2:
            await asyncio.sleep(0)
        busy = await _exchange(app, authorized, _chunks(b"{}"))
        assert busy[0]["status"] == 503
        assert (b"cache-control", b"no-store") in busy[0]["headers"]
        denied = await _exchange(app, [(b"host", b"mcp.example.com")], _chunks())
        assert denied[0]["status"] == 401
        release.set()
        for task in held:
            assert (await task)[0]["status"] == 200
        after = await _exchange(app, authorized, _chunks(b"{}"))
        assert after[0]["status"] == 200

    _run(check())


def test_failed_application_frees_its_place(
    config: HTTPConfig, signing_keys: tuple[Any, Any]
) -> None:
    """A request that fails with 500 does not keep its place."""

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        raise RuntimeError(SENTINEL)

    async def check() -> None:
        app = _gate(config, signing_keys, downstream, max_requests=1)
        for _ in range(3):
            messages = await _exchange(
                app, _authorized(config, signing_keys), _chunks(b"{}")
            )
            assert messages[0]["status"] == 500

    _run(check())


def _events(output: str) -> list[str]:
    return [
        line.removeprefix("knowledge-server-http event category=")
        for line in output.splitlines()
        if line.startswith("knowledge-server-http event ")
    ]


def test_key_and_assertion_events(
    config: HTTPConfig, signing_keys: tuple[Any, Any], http_logs: io.StringIO
) -> None:
    """Key and assertion failures log fixed categories before the request line.

    A probe without an assertion logs no event, and no event contains the
    failure's details.
    """

    async def failing(url: str) -> dict[str, Any]:
        raise RuntimeError(SENTINEL)

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        raise AssertionError("Rejected requests must not dispatch")

    def gate(fetch: Any) -> HTTPApplication:
        return HTTPApplication(
            downstream,
            config,
            AssertionVerifier(config, CachedKeys(config, fetch=fetch)),
        )

    token = _token(signing_keys[0], config)
    headers = [
        (b"host", b"mcp.example.com"),
        (b"cf-access-jwt-assertion", token.encode()),
    ]
    other = _token(signing_keys[0], config, {"sub": SENTINEL})
    rejected = [headers[0], (b"cf-access-jwt-assertion", other.encode())]
    for app, request_headers in (
        (gate(failing), [(b"host", b"mcp.example.com")]),
        (gate(failing), headers),
        (gate(_keys(config, signing_keys).fetch), rejected),
    ):
        assert _run(_request(app, request_headers))[0]["status"] == 401
    output = http_logs.getvalue()
    request = "knowledge-server-http request method=POST status=401"
    assert [line.split(" latency_ms=")[0] for line in output.splitlines()] == [
        request,
        "knowledge-server-http event category=key-fetch-failed",
        "knowledge-server-http event category=assertion-rejected",
        request,
        "knowledge-server-http event category=assertion-rejected",
        request,
    ]
    for private in (SENTINEL, token, other, config.owner_subject, "Traceback"):
        assert private not in output


def test_tool_failure_event(
    config: HTTPConfig,
    signing_keys: tuple[Any, Any],
    tmp_path: Path,
    http_logs: io.StringIO,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unexpected tool exception logs only `tool-failed`.

    The client receives an `INTERNAL_ERROR` tool error inside HTTP 200.
    """
    import knowledge_server.adapter.server as tools

    def broken(*args: Any) -> None:
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(tools, "read_note", broken)
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("# Note\n", encoding="utf-8")
    app = create_http_app(
        PathPolicy(root),
        config,
        ripgrep=RG,
        key_fetch=_keys(config, signing_keys).fetch,
    )

    async def check() -> dict[str, Any]:
        async with (
            _lifespan(app),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://127.0.0.1:8000",
                headers={
                    "host": "mcp.example.com",
                    "cf-access-jwt-assertion": _token(signing_keys[0], config),
                    "accept": "application/json, text/event-stream",
                },
            ) as client,
        ):
            response = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "knowledge_read",
                        "arguments": {"path": "note.md"},
                    },
                },
            )
            assert response.status_code == 200
            return response.json()["result"]

    result = CallToolResult.model_validate(_run(check()))
    assert result.is_error
    content = result.content[0]
    assert isinstance(content, TextContent)
    assert json.loads(content.text)["code"] == "INTERNAL_ERROR"
    output = http_logs.getvalue()
    assert _events(output) == ["tool-failed"]
    for private in (SENTINEL, "knowledge_read", "RuntimeError", "server.py", "note.md"):
        assert private not in output


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write to any directory")
@pytest.mark.parametrize("inbox", ["missing", "file", "symlink", "read-only"])
def test_vault_mode_with_write_proposals_needs_a_writable_inbox(
    tmp_path: Path, inbox: str
) -> None:
    """The launcher stops with `inbox` unless the inbox is a writable directory.

    The root itself is read-only, so the `writable-root` check passes.
    """
    root = tmp_path / "invented"
    shutil.copytree(FIXTURES, root)
    target = root / "inbox"
    if inbox == "file":
        target.write_text("", encoding="utf-8")
    elif inbox == "symlink":
        (tmp_path / "elsewhere").mkdir()
        target.symlink_to(tmp_path / "elsewhere")
    elif inbox == "read-only":
        target.mkdir(mode=0o555)
    configuration = tmp_path / "config.toml"
    configuration.write_text(
        _config_text(root, "vault") + "write_proposals = true\n", encoding="utf-8"
    )
    root.chmod(0o555)
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "knowledge_server.adapter.http_main",
                "--config",
                str(configuration),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    finally:
        root.chmod(0o755)
        if inbox == "read-only":
            target.chmod(0o755)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "knowledge-server-http startup category=inbox\n"
