"""Verify Cloudflare Access assertions for the configured vault owner.

A Cloudflare Access assertion is a JSON Web Token (JWT) in the
`Cf-Access-Jwt-Assertion` header. Cloudflare signs it with RSA keys that it
publishes as a JSON Web Key Set (JWKS) at the team's certificate URL. This
module fetches and caches those keys within fixed limits, and accepts an
assertion only if its signature and claims match the configured owner. An
invalid assertion or a failed key retrieval leads to rejection.
"""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from knowledge_server.adapter.http_config import HTTPConfig
from knowledge_server.adapter.http_logging import EVENT_FORMAT

FETCH_TIMEOUT = 5.0
CACHE_TTL = 3600.0
REFRESH_INTERVAL = 30.0
MAX_KEY_BYTES = 65536
MAX_KEYS = 16
MAX_ASSERTION_BYTES = 16384
CLOCK_SKEW = 30

_logger = logging.getLogger("knowledge_server.http")

# Injecting this boundary lets every test use invented keys without a network.
KeyFetch = Callable[[str], Awaitable[dict[str, Any]]]


class JWKSFetcher:
    """Fetch the signing-key set over HTTPS within time and size limits.

    Redirects are refused, and proxy environment variables are ignored.

    Attributes:
        transport: Optional injected HTTP transport, used by offline tests.
    """

    def __init__(self, *, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        """Choose the HTTP transport.

        Args:
            transport: An injected transport; None uses HTTPS networking.
        """
        self.transport = transport

    async def __call__(self, url: str) -> dict[str, Any]:
        """Fetch one JSON key set within the total time and byte bounds.

        Args:
            url: The certificate endpoint derived from trusted configuration.

        Returns:
            The decoded JSON object; key suitability is checked by the cache.

        Raises:
            ValueError: If the response exceeds its byte limit or is not JSON.
            TypeError: If the JSON value is not an object.
            httpx2.HTTPError: If the request fails, returns an error status, or
                redirects.
            TimeoutError: If the total fetch deadline expires.
        """
        async with asyncio.timeout(FETCH_TIMEOUT):
            async with httpx2.AsyncClient(
                transport=self.transport,
                trust_env=False,
                follow_redirects=False,
                timeout=FETCH_TIMEOUT,
            ) as client:
                async with client.stream("GET", url) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(body) + len(chunk) > MAX_KEY_BYTES:
                            raise ValueError("Key response exceeded its limit.")
                        body.extend(chunk)
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise TypeError("Key response must be an object.")
                return result


class CachedKeys:
    """Cache Cloudflare's signing keys and limit how often they are refetched.

    Keys from a successful fetch stay fresh for `CACHE_TTL` seconds. An
    unknown key ID or a stale cache can start a refresh, but at most one fetch
    attempt starts every `REFRESH_INTERVAL` seconds, failed attempts included.

    Attributes:
        config: The settings that fix the only key URL, `config.jwks_url`.
        fetch: The function that fetches the key set; by default, a
            `JWKSFetcher`.
        clock: Monotonic clock for cache age and the refresh interval.
    """

    def __init__(
        self,
        config: HTTPConfig,
        *,
        fetch: KeyFetch | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create an empty key cache with no network activity.

        Args:
            config: The validated authorization settings.
            fetch: A trusted-source fetch function; tests inject invented keys.
            clock: Monotonic seconds; tests can advance cache time explicitly.
        """
        self.config = config
        self.fetch = JWKSFetcher() if fetch is None else fetch
        self.clock = clock
        self._keys: dict[str, jwt.PyJWK] = {}
        self._expires = float("-inf")
        self._next_refresh = float("-inf")
        self._lock = asyncio.Lock()

    async def get(self, kid: str) -> jwt.PyJWK | None:
        """Return a fresh trusted key, refreshing only within the cache limits.

        A fresh cached key is returned at once, even while a refresh runs.
        Refreshes run one at a time. A failed or refused refresh keeps the
        current keys; a successful one replaces the whole set. A failed
        refresh logs the fixed `key-fetch-failed` event and nothing else.

        Args:
            kid: An assertion's key ID; it selects a key but cannot change the
                URL that keys come from.

        Returns:
            The fresh key with this ID, or None if no such key is available.
        """
        # No await separates these reads, and a refresh replaces the keys and
        # their expiry with no await between them. On one event loop this check
        # therefore sees a consistent cache without waiting for the lock.
        if self.clock() < self._expires and kid in self._keys:
            return self._keys[kid]
        async with self._lock:
            now = self.clock()
            if now < self._expires and kid in self._keys:
                return self._keys[kid]
            if now < self._next_refresh:
                return None
            self._next_refresh = now + REFRESH_INTERVAL
            try:
                async with asyncio.timeout(FETCH_TIMEOUT):
                    data = await self.fetch(self.config.jwks_url)
                keys = _parse_keys(data)
            except Exception:  # noqa: BLE001
                # Source failures never open the gate or print response data.
                _logger.warning(EVENT_FORMAT, "key-fetch-failed")
                return None
            self._keys = keys
            self._expires = self.clock() + CACHE_TTL
            return keys.get(kid)


def _parse_keys(data: dict[str, Any]) -> dict[str, jwt.PyJWK]:
    """Return the usable RS256 signing keys of a raw key set by key ID.

    Public entries without a usable key ID, or that are not RSA signing keys
    for RS256, are skipped. The entry limit, the private-material check, and
    the duplicate key ID check still cover every entry.

    Args:
        data: The decoded key set from the trusted source.

    Returns:
        The usable keys; at least one is present.

    Raises:
        TypeError: If an entry is not an object.
        ValueError: If the key list is missing, empty, or too long, contains
            private key material or a repeated key ID, has an eligible RSA key
            outside the size bounds, or has no usable key.
        jwt.PyJWTError: If an eligible RSA entry cannot be parsed.
    """
    entries = data.get("keys")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_KEYS:
        raise ValueError("Invalid key set.")
    keys: dict[str, jwt.PyJWK] = {}
    identifiers: set[str] = set()
    private_fields = {"d", "p", "q", "dp", "dq", "qi", "oth", "k"}
    for entry in entries:
        if not isinstance(entry, dict):
            raise TypeError("Invalid key.")
        if private_fields.intersection(entry):
            raise ValueError("Private key material is not permitted.")
        kid = entry.get("kid")
        if isinstance(kid, str):
            if kid in identifiers:
                raise ValueError("Ambiguous key identifier.")
            identifiers.add(kid)
        if (
            not isinstance(kid, str)
            or not 1 <= len(kid) <= 256
            or entry.get("kty") != "RSA"
            or entry.get("use", "sig") != "sig"
            or entry.get("alg", "RS256") != "RS256"
        ):
            continue
        key = jwt.PyJWK.from_dict(entry, algorithm="RS256")
        if (
            not isinstance(key.key, RSAPublicKey)
            or not 2048 <= key.key.key_size <= 8192
        ):
            raise ValueError("Unsuitable RSA key size.")
        keys[kid] = key
    if not keys:
        raise ValueError("No usable signing key.")
    return keys


class AssertionVerifier:
    """Accept only a valid assertion for the configured vault owner.

    The assertion must be signed with RS256 by a trusted key and must have
    these claims: `iss` equal to `config.issuer`; `aud` containing
    `config.audience`, the AUD tag of the Access application; `sub`,
    Cloudflare's user ID, exactly equal to `config.owner_subject`; an `exp`
    expiry time; and `type` equal to "app". Expiry and the optional `nbf`
    and `iat` times are checked with `CLOCK_SKEW` seconds of tolerance. A
    `common_name` claim, which marks a service credential instead of a user,
    causes rejection.

    Attributes:
        config: Fixed issuer, audience, and owner settings.
        keys: The bounded trusted signing-key provider.
    """

    def __init__(self, config: HTTPConfig, keys: CachedKeys) -> None:
        """Bind verification to trusted configuration and a key provider.

        Args:
            config: Validated authorization settings.
            keys: The trusted-source key cache.
        """
        self.config = config
        self.keys = keys

    async def authorize(self, token: str) -> bool:
        """Return whether an assertion authorizes the configured owner.

        Before any key lookup, the token must be ASCII, at most
        `MAX_ASSERTION_BYTES` long, and declare RS256 with a key ID of 1 to
        256 characters. The required `exp` claim, and the `nbf` and `iat`
        claims when present, must be integers. An invalid assertion or a
        failed key lookup returns False instead of raising, and nothing is
        logged.

        Args:
            token: The value of the single `Cf-Access-Jwt-Assertion` header.

        Returns:
            True only if every check passes.
        """
        try:
            if not token.isascii() or not 1 <= len(token) <= MAX_ASSERTION_BYTES:
                return False
            header = jwt.get_unverified_header(token)
            kid = header.get("kid")
            if (
                header.get("alg") != "RS256"
                or not isinstance(kid, str)
                or not 1 <= len(kid) <= 256
            ):
                return False
            key = await self.keys.get(kid)
            if key is None:
                return False
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.config.issuer,
                audience=self.config.audience,
                subject=self.config.owner_subject,
                leeway=CLOCK_SKEW,
                options={
                    "require": ["iss", "aud", "exp", "sub", "type"],
                    "enforce_minimum_key_length": True,
                },
            )
            return (
                claims["type"] == "app"
                and claims["sub"] == self.config.owner_subject
                and "common_name" not in claims
                and all(
                    type(claims[name]) is int
                    for name in ("exp", "nbf", "iat")
                    if name in claims
                )
            )
        except Exception:  # noqa: BLE001
            return False
