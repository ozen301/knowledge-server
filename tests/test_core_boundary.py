"""Tests for the core package boundary."""

import subprocess
import sys


def test_core_import_does_not_load_the_mcp_sdk() -> None:
    """Core policy remains reusable independently from the protocol adapter."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import knowledge_server.core.paths; "
                "assert not any(name == 'mcp' or name.startswith('mcp.') for name in sys.modules)"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
