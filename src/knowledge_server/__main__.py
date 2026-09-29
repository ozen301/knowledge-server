"""Start the knowledge server over stdio.

Run it as `knowledge-server` or `python -m knowledge_server`. Stdout carries
only MCP messages; diagnostic messages go to stderr.
"""

import logging
import shutil
import sys

from knowledge_server.adapter.server import create_server
from knowledge_server.config import ConfigurationError, load_config
from knowledge_server.core.paths import PathPolicy

_logger = logging.getLogger("knowledge_server")


def main() -> int:
    """Check the configuration, then serve the tools until stdin closes.

    Returns:
        The process exit status: 0 after a normal shutdown, 1 if the vault
        root is unusable or `rg` is not found on `PATH`, in which case the
        server never starts.
    """
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="knowledge-server %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config()
        policy = PathPolicy(config.root)
    except ConfigurationError as error:
        _logger.error("%s", error)
        return 1
    except OSError:
        _logger.error("KNOWLEDGE_ROOT could not be opened.")
        return 1
    ripgrep = shutil.which("rg")
    if ripgrep is None:
        _logger.error("ripgrep (rg) was not found on PATH.")
        return 1
    create_server(policy, ripgrep=ripgrep).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
