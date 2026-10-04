"""Validated HTTP configuration and explicit synthetic trial configuration."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from knowledge_server.config import ConfigurationError, load_config

_DOMAIN = re.compile(
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\Z"
)
_TEAM = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com\Z")


def _text(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value == value.strip()
        and len(value) <= 1024
        and all(ord(char) >= 33 and ord(char) != 127 for char in value)
    )


def _origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme in {"http", "https"}
            and parsed.netloc != ""
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and parsed.port != 0
            and _text(value)
            and "*" not in value
        )
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class HTTPConfig:
    """Settings for the HTTP gate and the owner assertion check.

    Creating an instance validates every field.

    Attributes:
        team_domain: The Cloudflare Zero Trust team domain, such as
            `myteam.cloudflareaccess.com`, without a scheme. It determines the
            trusted issuer and the signing-key URL.
        audience: The Access application's AUD tag, which identifies the
            application an assertion was issued for; the assertion's `aud`
            claim must contain it.
        owner_subject: The vault owner's Cloudflare user ID, verified
            privately; the assertion's signed `sub` claim must equal it.
        public_host: The public MCP hostname, such as `mcp.example.com`,
            without a scheme or port.
        port: The port of the listener on `127.0.0.1`.
        allowed_origins: Exact `Origin` header values to accept, such as
            `https://example.com`. A request without `Origin` passes this
            check; with no values, any request that sends `Origin` fails it.
    """

    team_domain: str
    audience: str
    owner_subject: str
    public_host: str
    port: int = 8000
    allowed_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Reject unusable or unsafe values before an application can start.

        Raises:
            ConfigurationError: If any configuration field is invalid.
        """
        if (
            not all(
                _text(value)
                for value in (
                    self.team_domain,
                    self.audience,
                    self.owner_subject,
                    self.public_host,
                )
            )
            or not _TEAM.fullmatch(self.team_domain)
            or not _DOMAIN.fullmatch(self.public_host)
            or len(self.public_host) > 253
            or "." not in self.public_host
            or type(self.port) is not int
            or not 1 <= self.port <= 65535
            or not isinstance(self.allowed_origins, tuple)
            or not all(
                isinstance(value, str) and _origin(value)
                for value in self.allowed_origins
            )
        ):
            raise ConfigurationError("HTTP configuration is invalid.")

    @property
    def issuer(self) -> str:
        """Return the exact trusted assertion issuer.

        Returns:
            `https://` followed by `team_domain`, with no trailing slash. The
            assertion's `iss` claim must equal it.
        """
        return f"https://{self.team_domain}"

    @property
    def jwks_url(self) -> str:
        """Return the only URL from which signing keys are fetched.

        Returns:
            `<issuer>/cdn-cgi/access/certs`, where Cloudflare publishes the
            team's public signing keys.
        """
        return f"{self.issuer}/cdn-cgi/access/certs"

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        """Return the exact public and loopback Host values.

        Returns:
            The public hostname, with or without port 443, and `127.0.0.1`
            and `localhost`, with or without the listener port.
        """
        return (
            self.public_host,
            f"{self.public_host}:443",
            "127.0.0.1",
            "localhost",
            f"127.0.0.1:{self.port}",
            f"localhost:{self.port}",
        )


@dataclass(frozen=True, slots=True)
class TrialConfig:
    """An explicitly selected synthetic root and its HTTP settings.

    Attributes:
        root: Resolved absolute path to a dedicated invented-note directory.
        http: The validated transport and authorization settings.
    """

    root: Path
    http: HTTPConfig


def load_trial_config(path: Path) -> TrialConfig:
    """Load the synthetic trial settings from one explicit TOML file.

    The file must set `mode = "synthetic"`, `root`, and the required
    `HTTPConfig` fields; unknown fields are rejected. `KNOWLEDGE_ROOT` and
    `.env` files are not read, but `root` passes the same validation as
    `KNOWLEDGE_ROOT`.

    Args:
        path: Absolute path to a private synthetic trial configuration file
            of at most 16 KiB.

    Returns:
        Validated settings; the caller must also verify the synthetic contents.

    Raises:
        ConfigurationError: If the file or any setting is invalid.
    """
    try:
        if not path.is_absolute():
            raise ValueError
        with path.open("rb") as source:
            raw = source.read(16385)
        if len(raw) > 16384:
            raise ValueError
        values = tomllib.loads(raw.decode("utf-8"))
        if values.pop("mode", None) != "synthetic":
            raise ValueError
        root_value = values.pop("root")
        if not isinstance(root_value, str):
            raise TypeError
        root = load_config({"KNOWLEDGE_ROOT": root_value}).root
        origins = values.pop("allowed_origins", [])
        if not isinstance(origins, list):
            raise TypeError
        http = HTTPConfig(**values, allowed_origins=tuple(origins))
        return TrialConfig(root, http)
    except OSError, ValueError, TypeError, KeyError, ConfigurationError:
        raise ConfigurationError("Synthetic HTTP configuration is invalid.") from None
