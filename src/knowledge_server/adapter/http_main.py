"""Launch the protected loopback HTTP server in an explicit mode."""

import argparse
import logging
import os
import shutil
import stat
from pathlib import Path

import uvicorn

from knowledge_server.adapter.http import create_http_app
from knowledge_server.adapter.http_config import load_launch_config
from knowledge_server.adapter.http_logging import STARTUP_FORMAT, configure_http_logging
from knowledge_server.adapter.synthetic import verify_synthetic_root
from knowledge_server.config import ConfigurationError
from knowledge_server.core.paths import PathPolicy
from knowledge_server.core.writer import INBOX

_logger = logging.getLogger("knowledge_server.http")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default diagnostics echo untrusted argument values.
        raise ConfigurationError("HTTP configuration is required.")


def main(argv: list[str] | None = None) -> int:
    """Validate the configuration and root, then run on IPv4 loopback.

    In `synthetic` mode, the root must hold exactly the packaged sample notes.
    In `vault` mode, the process must not be able to write to the root
    directory; with write proposals enabled, it must be able to write to the
    root's inbox, a real directory. The server then runs until it receives a
    signal. After SIGINT (Ctrl-C), Uvicorn shuts down gracefully and this
    function returns zero. After SIGTERM, Uvicorn shuts down gracefully and
    then re-raises the signal, so the process ends by SIGTERM instead of
    returning.

    Args:
        argv: Optional explicit command arguments; None uses process arguments.

    Returns:
        Zero after a SIGINT shutdown or a help request, or one after a startup
        or runtime failure, which logs only a fixed category.
    """
    configure_http_logging()
    parser = _Parser(description="Run the protected loopback HTTP server.")
    parser.add_argument("--config", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        launch = load_launch_config(args.config)
        if launch.mode == "synthetic":
            verify_synthetic_root(launch.root)
        elif os.access(launch.root, os.W_OK):
            _logger.error(STARTUP_FORMAT, "writable-root")
            return 1
        elif launch.write_proposals and not _inbox_writable(launch.root):
            _logger.error(STARTUP_FORMAT, "inbox")
            return 1
        policy = PathPolicy(launch.root)
        ripgrep = shutil.which("rg")
        if ripgrep is None:
            _logger.error(STARTUP_FORMAT, "missing-ripgrep")
            return 1
        app = create_http_app(
            policy,
            launch.http,
            ripgrep=ripgrep,
            write_proposals=launch.write_proposals,
        )
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=launch.http.port,
            access_log=False,
            proxy_headers=False,
            log_config=None,
            server_header=False,
            lifespan="on",
        )
    except SystemExit as error:
        # Uvicorn exits directly after startup failures; keep its status and
        # diagnostics consistent with the rest of this launcher. Help exits
        # successfully through argparse before any server starts.
        if error.code == 0:
            return 0
        _logger.error(STARTUP_FORMAT, "runtime")
        return 1
    except ConfigurationError, OSError, ValueError:
        _logger.error(STARTUP_FORMAT, "configuration")
        return 1
    except Exception:  # noqa: BLE001
        _logger.error(STARTUP_FORMAT, "runtime")
        return 1
    return 0


def _inbox_writable(root: Path) -> bool:
    """Return whether the root's inbox is a real directory the process can write."""
    inbox = root / INBOX
    try:
        mode = inbox.lstat().st_mode
    except OSError:
        return False
    return stat.S_ISDIR(mode) and os.access(inbox, os.W_OK | os.X_OK)


if __name__ == "__main__":
    raise SystemExit(main())
