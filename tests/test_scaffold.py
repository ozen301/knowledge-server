"""Smoke tests for the installable package and supported MCP SDK transport."""

import asyncio
import importlib.metadata
import sys
from pathlib import Path

from mcp import Client, StdioServerParameters


def test_package_is_installed() -> None:
    """The distribution installs the public package at the resolved version."""
    import knowledge_server

    assert knowledge_server.__name__ == "knowledge_server"
    assert importlib.metadata.version("knowledge-server")


def test_sdk_stdio_server_starts_lists_no_tools_and_stops(tmp_path: Path) -> None:
    """A temporary SDK server supports a complete stdio client session."""
    completion_marker = tmp_path / "server-stopped"
    server_program = f"""
from pathlib import Path
from mcp.server.mcpserver import MCPServer

server = MCPServer(name=\"scaffold-smoke\", version=\"0.0.0\")
server.run(\"stdio\")
Path({str(completion_marker)!r}).write_text(\"stopped\", encoding=\"utf-8\")
"""
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-c", server_program],
    )

    async def exercise_server() -> None:
        async with Client(parameters) as client:
            tools = await client.list_tools()
            assert tools.tools == []

    asyncio.run(asyncio.wait_for(exercise_server(), timeout=8))
    assert completion_marker.read_text(encoding="utf-8") == "stopped"
