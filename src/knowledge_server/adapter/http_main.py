"""Launch the protected loopback HTTP server with a verified synthetic root."""

import argparse
import logging
import shutil
from pathlib import Path

import uvicorn

from knowledge_server.adapter.http import create_http_app
from knowledge_server.adapter.http_config import load_trial_config
from knowledge_server.adapter.http_logging import STARTUP_FORMAT, configure_http_logging
from knowledge_server.adapter.synthetic import verify_synthetic_root
from knowledge_server.config import ConfigurationError
from knowledge_server.core.paths import PathPolicy

_logger = logging.getLogger("knowledge_server.http")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default diagnostics echo untrusted argument values.
        raise ConfigurationError("Synthetic HTTP configuration is required.")


def main(argv: list[str] | None = None) -> int:
    """Validate synthetic configuration, then run on IPv4 loopback.

    The server runs until it receives a signal. After SIGINT (Ctrl-C), Uvicorn
    shuts down gracefully and this function returns zero. After SIGTERM,
    Uvicorn shuts down gracefully and then re-raises the signal, so the
    process ends by SIGTERM instead of returning.

    Args:
        argv: Optional explicit command arguments; None uses process arguments.

    Returns:
        Zero after a SIGINT shutdown or a help request, or one after a startup
        or runtime failure, which logs only a fixed category.
    """
    configure_http_logging()
    parser = _Parser(description="Run a protected synthetic-only HTTP trial.")
    parser.add_argument("--config", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
        trial = load_trial_config(args.config)
        verify_synthetic_root(trial.root)
        policy = PathPolicy(trial.root)
        ripgrep = shutil.which("rg")
        if ripgrep is None:
            _logger.error(STARTUP_FORMAT, "missing-ripgrep")
            return 1
        app = create_http_app(policy, trial.http, ripgrep=ripgrep)
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=trial.http.port,
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


if __name__ == "__main__":
    raise SystemExit(main())
