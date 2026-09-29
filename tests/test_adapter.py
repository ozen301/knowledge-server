"""Tests for the MCP adapter and the stdio entry point.

In-process tests connect an SDK client directly to the server object, so they
can replace core functions and capture log records. Subprocess tests start the
real entry point over stdio, as an MCP host does.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import textwrap
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters
from mcp_types import CallToolResult, TextContent

from knowledge_server.adapter import server as server_module
from knowledge_server.adapter.server import create_server
from knowledge_server.core.models import (
    DomainErrorCode,
    InfoRequest,
    InfoResult,
    KnowledgeError,
    ListRequest,
    ListResult,
    ReadRequest,
    ReadResult,
    SearchRequest,
    SearchResult,
)
from knowledge_server.core.paths import PathPolicy
from knowledge_server.core.reader import list_directory, note_info, read_note

_FOUND_RG = shutil.which("rg")
if _FOUND_RG is None:
    raise RuntimeError("ripgrep (rg) must be installed to run the adapter tests")
RG: str = _FOUND_RG

SECRET = "SENTINEL-SECRET"
PRIVATE_TEXT = "PRIVATE-EXCEPTION-TEXT"
QUERY_TEXT = "QUERY-PRIVATE-TEXT"
TOOL_NAMES = {"knowledge_search", "knowledge_read", "knowledge_list", "knowledge_info"}
INVALID_ARGUMENT = {
    "code": "INVALID_ARGUMENT",
    "message": KnowledgeError(DomainErrorCode.INVALID_ARGUMENT).message,
}
INTERNAL_ERROR = {
    "code": "INTERNAL_ERROR",
    "message": KnowledgeError(DomainErrorCode.INTERNAL_ERROR).message,
}


def _write(root: Path, relative: str, data: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A small vault with a hidden note and a symlink to an outside file."""
    root = tmp_path / "vault"
    _write(root, "notes/alpha.md", "# Alpha\nUse ECC memory.\nthird line\n")
    _write(root, "notes/beta.md", "beta\n")
    (root / "empty").mkdir()
    _write(root, ".hidden/secret.md", f"{SECRET}\n")
    outside = _write(tmp_path, "outside.md", f"{SECRET}\n")
    (root / "link.md").symlink_to(outside)
    return root


def _snapshot(root: Path) -> dict[str, tuple[int, bytes | str]]:
    """Record every entry below the root, to show that calls change nothing."""
    entries: dict[str, tuple[int, bytes | str]] = {}
    for path in sorted(root.rglob("*")):
        status = path.lstat()
        if path.is_symlink():
            content: bytes | str = os.readlink(path)
        elif path.is_file():
            content = path.read_bytes()
        else:
            content = b""
        entries[str(path.relative_to(root))] = (status.st_mtime_ns, content)
    return entries


def _run(coroutine: Awaitable[Any]) -> Any:
    async def bounded() -> Any:
        return await asyncio.wait_for(coroutine, timeout=20)

    return asyncio.run(bounded())


async def _call_in_process(
    root: Path, calls: list[tuple[str, dict[str, Any]]]
) -> list[CallToolResult]:
    server = create_server(PathPolicy(root), ripgrep=RG)
    async with Client(server) as client:
        return [await client.call_tool(name, arguments) for name, arguments in calls]


def _text(result: CallToolResult) -> str:
    assert len(result.content) == 1
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


def _success(result: CallToolResult) -> dict[str, Any]:
    """Check a successful result and return its structured content."""
    assert result.is_error is False
    assert result.structured_content is not None
    assert json.loads(_text(result)) == result.structured_content
    return result.structured_content


def _error(result: CallToolResult) -> dict[str, Any]:
    """Check an error result and return its decoded error object."""
    assert result.is_error is True
    assert result.structured_content is None
    return json.loads(_text(result))


def _dump(result: Any) -> dict[str, Any]:
    return result.model_dump(mode="json")


# Tool discovery


def test_server_lists_four_tools_with_contract_schemas(vault: Path) -> None:
    """Schemas come from the request and result models the contract defines."""

    async def list_tools() -> Any:
        async with Client(create_server(PathPolicy(vault), ripgrep=RG)) as client:
            return await client.list_tools()

    tools = {tool.name: tool for tool in _run(list_tools()).tools}
    assert set(tools) == TOOL_NAMES

    models = {
        "knowledge_search": (SearchRequest, SearchResult),
        "knowledge_read": (ReadRequest, ReadResult),
        "knowledge_list": (ListRequest, ListResult),
        "knowledge_info": (InfoRequest, InfoResult),
    }
    for name, (request_model, result_model) in models.items():
        assert tools[name].input_schema == request_model.model_json_schema()
        assert tools[name].output_schema == result_model.model_json_schema()
        assert tools[name].description

    def properties(schema: dict[str, Any]) -> set[str]:
        return set(schema["properties"])

    assert properties(tools["knowledge_search"].input_schema) == {
        "query",
        "path",
        "max_results",
        "case_sensitive",
        "mode",
    }
    assert tools["knowledge_search"].input_schema["required"] == ["query"]
    assert properties(tools["knowledge_read"].input_schema) == {
        "path",
        "start_line",
        "end_line",
    }
    assert tools["knowledge_read"].input_schema["required"] == ["path"]
    assert properties(tools["knowledge_list"].input_schema) == {
        "path",
        "offset",
        "limit",
    }
    assert "required" not in tools["knowledge_list"].input_schema
    assert properties(tools["knowledge_info"].input_schema) == {"path"}
    assert tools["knowledge_info"].input_schema["required"] == ["path"]
    for tool in tools.values():
        assert tool.input_schema["additionalProperties"] is False

    assert properties(tools["knowledge_search"].output_schema or {}) == {
        "mode",
        "matches",
        "truncated",
        "incomplete",
        "skipped",
    }
    assert properties(tools["knowledge_read"].output_schema or {}) == {
        "path",
        "content",
        "start_line",
        "end_line",
        "total_lines",
        "next_line",
        "truncated",
        "content_sha256",
    }
    assert properties(tools["knowledge_list"].output_schema or {}) == {
        "path",
        "entries",
        "next_offset",
        "truncated",
    }
    assert properties(tools["knowledge_info"].output_schema or {}) == {
        "path",
        "size_bytes",
        "modified_at",
        "line_count",
        "content_sha256",
        "readable",
        "unreadable_reason",
    }


def test_every_tool_is_annotated_read_only(vault: Path) -> None:
    """Annotations describe tools that only read and never change state."""

    async def list_tools() -> Any:
        async with Client(create_server(PathPolicy(vault), ripgrep=RG)) as client:
            return await client.list_tools()

    for tool in _run(list_tools()).tools:
        annotations = tool.annotations
        assert annotations is not None
        assert annotations.read_only_hint is True
        assert annotations.destructive_hint is False
        assert annotations.idempotent_hint is True
        assert annotations.open_world_hint is False
        assert annotations.title


# Successful calls


def test_successful_calls_return_core_results_as_structured_content_and_json(
    vault: Path,
) -> None:
    """Each tool returns the core result, with equivalent JSON text."""
    before = _snapshot(vault)
    search, read, listing, info = _run(
        _call_in_process(
            vault,
            [
                ("knowledge_search", {"query": "ECC"}),
                ("knowledge_read", {"path": "notes/alpha.md", "end_line": 2}),
                ("knowledge_list", {"path": "notes"}),
                ("knowledge_info", {"path": "notes/alpha.md"}),
            ],
        )
    )
    policy = PathPolicy(vault)

    search_content = _success(search)
    assert [(match["path"], match["line"]) for match in search_content["matches"]] == [
        ("notes/alpha.md", 2)
    ]
    assert _success(read) == _dump(
        read_note(policy, ReadRequest(path="notes/alpha.md", end_line=2))
    )
    assert _success(listing) == _dump(list_directory(policy, ListRequest(path="notes")))
    assert _success(info) == _dump(
        note_info(policy, InfoRequest(path="notes/alpha.md"))
    )
    assert _snapshot(vault) == before


def test_empty_results_are_successes_not_errors(vault: Path) -> None:
    """A search without matches and an empty listing are ordinary results."""
    search, listing, read = _run(
        _call_in_process(
            vault,
            [
                ("knowledge_search", {"query": "no such text"}),
                ("knowledge_list", {"path": "empty"}),
                ("knowledge_read", {"path": "notes/beta.md", "start_line": 5}),
            ],
        )
    )
    assert _success(search)["matches"] == []
    assert _success(listing)["entries"] == []
    assert _success(read)["content"] == ""


# Argument and domain errors


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("knowledge_read", {}),
        ("knowledge_read", {"path": 5}),
        ("knowledge_read", {"path": None}),
        ("knowledge_read", {"path": "notes/alpha.md", "start_line": "2"}),
        ("knowledge_read", {"path": "notes/alpha.md", "start_line": True}),
        ("knowledge_read", {"path": "notes/alpha.md", "start_line": 0}),
        ("knowledge_read", {"path": "notes/alpha.md", "start_line": 5, "end_line": 2}),
        ("knowledge_read", {"path": "notes/alpha.md", "end_line": 201}),
        ("knowledge_read", {"path": "notes/alpha.md", "unknown": 1}),
        ("knowledge_search", {}),
        ("knowledge_search", {"query": "   "}),
        ("knowledge_search", {"query": "two\nlines"}),
        ("knowledge_search", {"query": QUERY_TEXT * 40}),
        ("knowledge_search", {"query": "ECC", "max_results": 51}),
        ("knowledge_search", {"query": "ECC", "max_results": "[1]"}),
        ("knowledge_search", {"query": "ECC", "case_sensitive": "true"}),
        ("knowledge_search", {"query": "ECC", "mode": "regex"}),
        ("knowledge_list", {"offset": -1}),
        ("knowledge_list", {"limit": 0}),
        ("knowledge_list", {"limit": 201}),
        ("knowledge_list", {"limit": 1.0}),
        ("knowledge_info", {"path": ["notes/alpha.md"]}),
    ],
)
def test_invalid_arguments_return_invalid_argument(
    vault: Path, tool: str, arguments: dict[str, Any]
) -> None:
    """Arguments the request model rejects produce the contract error."""
    (result,) = _run(_call_in_process(vault, [(tool, arguments)]))
    assert _error(result) == INVALID_ARGUMENT
    assert QUERY_TEXT not in _text(result)


@pytest.mark.parametrize(
    ("tool", "arguments", "code"),
    [
        ("knowledge_read", {"path": ".hidden/secret.md"}, "ACCESS_DENIED"),
        ("knowledge_read", {"path": "link.md"}, "ACCESS_DENIED"),
        ("knowledge_read", {"path": "../outside.md"}, "INVALID_PATH"),
        ("knowledge_read", {"path": "missing.md"}, "NOT_FOUND"),
        ("knowledge_read", {"path": "notes"}, "UNSUPPORTED_TYPE"),
        ("knowledge_info", {"path": "/etc/hostname.md"}, "INVALID_PATH"),
        ("knowledge_info", {"path": "link.md"}, "ACCESS_DENIED"),
        ("knowledge_list", {"path": "notes/alpha.md"}, "NOT_A_DIRECTORY"),
        ("knowledge_list", {"path": ".hidden"}, "ACCESS_DENIED"),
        ("knowledge_search", {"query": SECRET, "path": ".hidden"}, "ACCESS_DENIED"),
        ("knowledge_search", {"query": "ECC", "path": "missing"}, "NOT_FOUND"),
    ],
)
def test_domain_errors_return_code_and_safe_message(
    vault: Path, tool: str, arguments: dict[str, Any], code: str
) -> None:
    """Policy is enforced by the core, whatever the tool annotations say."""
    (result,) = _run(_call_in_process(vault, [(tool, arguments)]))
    assert _error(result) == {
        "code": code,
        "message": KnowledgeError(DomainErrorCode(code)).message,
    }
    assert SECRET not in _text(result)
    assert str(vault) not in _text(result)


def test_search_does_not_reveal_hidden_or_symlinked_notes(vault: Path) -> None:
    """A whole-vault search skips content outside the visibility policy."""
    (result,) = _run(_call_in_process(vault, [("knowledge_search", {"query": SECRET})]))
    content = _success(result)
    assert content["matches"] == []
    assert content["incomplete"] is False


# Unexpected exceptions


def _fail_once[**P, R](
    function: Callable[P, R], error: Exception
) -> tuple[Callable[P, R], list[int]]:
    """Wrap a function so that its first call raises and later calls succeed."""
    calls: list[int] = []

    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        calls.append(1)
        if len(calls) == 1:
            raise error
        return function(*args, **kwargs)

    return wrapper, calls


def _fail_once_async[**P, R](
    function: Callable[P, Awaitable[R]], error: Exception
) -> tuple[Callable[P, Awaitable[R]], list[int]]:
    """Wrap an async function so that its first call raises."""
    calls: list[int] = []

    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        calls.append(1)
        if len(calls) == 1:
            raise error
        return await function(*args, **kwargs)

    return wrapper, calls


@pytest.mark.parametrize(
    ("tool", "function_name", "arguments"),
    [
        ("knowledge_search", "search_notes", {"query": f"ECC {QUERY_TEXT}"}),
        ("knowledge_read", "read_note", {"path": "notes/alpha.md"}),
        ("knowledge_list", "list_directory", {"path": "notes"}),
        ("knowledge_info", "note_info", {"path": "notes/alpha.md"}),
    ],
)
def test_unexpected_exception_returns_safe_internal_error(
    vault: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    tool: str,
    function_name: str,
    arguments: dict[str, Any],
) -> None:
    """Neither the response nor the logs expose the exception text or query."""
    caplog.set_level(logging.DEBUG)
    error = RuntimeError(f"{vault}/notes/alpha.md {PRIVATE_TEXT}")
    original = getattr(server_module, function_name)
    if function_name == "search_notes":
        replacement, calls = _fail_once_async(original, error)
    else:
        replacement, calls = _fail_once(original, error)
    monkeypatch.setattr(server_module, function_name, replacement)

    failed, succeeded = _run(
        _call_in_process(vault, [(tool, arguments), (tool, arguments)])
    )

    assert len(calls) == 2
    assert _error(failed) == INTERNAL_ERROR
    assert PRIVATE_TEXT not in _text(failed)
    assert str(vault) not in _text(failed)
    _success(succeeded)
    assert any(
        record.levelno >= logging.ERROR and tool in record.getMessage()
        for record in caplog.records
    )
    assert PRIVATE_TEXT not in caplog.text
    assert QUERY_TEXT not in caplog.text
    assert str(vault) not in caplog.text


# Stdio entry point


def _launcher(prelude: str = "") -> str:
    """Return a program that runs the entry point and records its exit status."""
    return textwrap.dedent(
        """
        import sys
        from pathlib import Path
        {prelude}
        from knowledge_server.__main__ import main

        status = main()
        Path(sys.argv[1]).write_text(str(status), encoding="utf-8")
        sys.exit(status)
        """
    ).format(prelude=textwrap.dedent(prelude))


class _Launch:
    """Paths for one server process started through a shell wrapper.

    The wrapper copies the server's stdout to a file with `tee` and sends its
    stderr to another file, so a test can inspect both streams.
    """

    def __init__(
        self, directory: Path, root: Path, *, prelude: str = "", path: str | None = None
    ) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.stdout = directory / "stdout.log"
        self.stderr = directory / "stderr.log"
        self.status = directory / "status"
        program = directory / "launcher.py"
        program.write_text(_launcher(prelude), encoding="utf-8")
        environment = {"KNOWLEDGE_ROOT": str(root)}
        if path is not None:
            environment["PATH"] = path
        self.parameters = StdioServerParameters(
            command="/bin/sh",
            args=[
                "-c",
                '"$0" "$1" "$2" 2>"$3" | tee "$4"',
                sys.executable,
                str(program),
                str(self.status),
                str(self.stderr),
                str(self.stdout),
            ],
            env=environment,
        )

    def check_stdout_is_protocol_only(self) -> None:
        lines = self.stdout.read_text(encoding="utf-8").splitlines()
        assert lines
        for line in lines:
            assert json.loads(line)["jsonrpc"] == "2.0"


def _descendants(pid: int) -> set[int]:
    found: set[int] = set()
    pending = [pid]
    while pending:
        current = pending.pop()
        for task in Path(f"/proc/{current}/task").iterdir():
            children = (task / "children").read_text().split()
            for child in map(int, children):
                if child not in found:
                    found.add(child)
                    pending.append(child)
    return found


def _tcp_socket_inodes() -> set[str]:
    inodes: set[str] = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        for line in Path(table).read_text().splitlines()[1:]:
            inodes.add(line.split()[9])
    return inodes


def _socket_inodes(pid: int) -> set[str]:
    inodes: set[str] = set()
    for descriptor in Path(f"/proc/{pid}/fd").iterdir():
        try:
            target = os.readlink(descriptor)
        except OSError:
            continue
        if target.startswith("socket:["):
            inodes.add(target.removeprefix("socket:[").removesuffix("]"))
    return inodes


@pytest.mark.skipif(not Path("/proc/self/task").is_dir(), reason="needs Linux /proc")
def test_stdio_session_serves_four_tools_and_exits_cleanly(
    vault: Path, tmp_path: Path
) -> None:
    """A real SDK client drives the entry point over stdio."""
    launch = _Launch(tmp_path / "launch", vault)

    async def exercise() -> None:
        async with Client(launch.parameters) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == TOOL_NAMES

            search = await client.call_tool("knowledge_search", {"query": "ECC"})
            assert _success(search)["matches"][0]["path"] == "notes/alpha.md"
            read = await client.call_tool("knowledge_read", {"path": "notes/alpha.md"})
            assert _success(read)["total_lines"] == 3
            listing = await client.call_tool("knowledge_list", {})
            assert _success(listing)["entries"][0] == {
                "path": "empty",
                "kind": "directory",
            }
            info = await client.call_tool("knowledge_info", {"path": "notes/beta.md"})
            assert _success(info)["line_count"] == 1

            missing = await client.call_tool("knowledge_read", {"path": "missing.md"})
            assert _error(missing)["code"] == "NOT_FOUND"
            invalid = await client.call_tool("knowledge_list", {"limit": "5"})
            assert _error(invalid) == INVALID_ARGUMENT
            again = await client.call_tool("knowledge_info", {"path": "notes/beta.md"})
            _success(again)

            server_processes = _descendants(os.getpid())
            assert server_processes
            tcp = _tcp_socket_inodes()
            for pid in server_processes:
                assert not _socket_inodes(pid) & tcp

    _run(exercise())
    assert launch.status.read_text(encoding="utf-8") == "0"
    launch.check_stdout_is_protocol_only()


def test_stdio_unexpected_exception_is_logged_without_private_text(
    vault: Path, tmp_path: Path
) -> None:
    """Diagnostic logging goes to stderr and does not break later calls."""
    prelude = f"""
    from knowledge_server.adapter import server as _server

    _original = _server.note_info
    _calls = []

    def _fail_once(*args, **kwargs):
        _calls.append(1)
        if len(_calls) == 1:
            raise RuntimeError({f"{vault}/notes/alpha.md {PRIVATE_TEXT}"!r})
        return _original(*args, **kwargs)

    _server.note_info = _fail_once
    """
    launch = _Launch(tmp_path / "launch", vault, prelude=prelude)

    async def exercise() -> None:
        async with Client(launch.parameters) as client:
            failed = await client.call_tool(
                "knowledge_info", {"path": "notes/alpha.md"}
            )
            assert _error(failed) == INTERNAL_ERROR
            assert PRIVATE_TEXT not in _text(failed)
            succeeded = await client.call_tool(
                "knowledge_info", {"path": "notes/alpha.md"}
            )
            _success(succeeded)

    _run(exercise())
    assert launch.status.read_text(encoding="utf-8") == "0"
    launch.check_stdout_is_protocol_only()
    stderr = launch.stderr.read_text(encoding="utf-8")
    assert "knowledge_info" in stderr
    assert PRIVATE_TEXT not in stderr
    assert str(vault) not in stderr


def test_stdio_cancelled_search_kills_ripgrep(vault: Path, tmp_path: Path) -> None:
    """Cancelling a search request stops the ripgrep process it started."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    pid_file = tmp_path / "rg.pid"
    fake_rg = fake_bin / "rg"
    fake_rg.write_text(
        f"#!/bin/sh\nPATH=/usr/bin:/bin\necho $$ > {pid_file}\nexec sleep 30\n",
        encoding="utf-8",
    )
    fake_rg.chmod(0o755)
    launch = _Launch(tmp_path / "launch", vault, path=f"{fake_bin}:/usr/bin:/bin")

    async def exercise() -> None:
        async with Client(launch.parameters) as client:
            call = asyncio.create_task(
                client.call_tool("knowledge_search", {"query": "ECC"})
            )
            while not pid_file.exists() or not pid_file.read_text().strip():
                assert not call.done()
                await asyncio.sleep(0.05)
            pid = int(pid_file.read_text())
            cancelled_at = time.monotonic()
            call.cancel()
            with pytest.raises(asyncio.CancelledError):
                await call

            while _process_exists(pid):
                assert time.monotonic() - cancelled_at < 3
                await asyncio.sleep(0.05)

            info = await client.call_tool("knowledge_info", {"path": "notes/beta.md"})
            _success(info)

    _run(exercise())
    assert launch.status.read_text(encoding="utf-8") == "0"


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# Startup failures


def _start(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = {key: value for key, value in os.environ.items() if key != "KNOWLEDGE_ROOT"}
    return subprocess.run(
        [sys.executable, "-m", "knowledge_server"],
        env=base | environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


@pytest.mark.parametrize(
    "environment",
    [
        {},
        {"KNOWLEDGE_ROOT": "relative/vault"},
        {"KNOWLEDGE_ROOT": "/nonexistent/vault"},
    ],
)
def test_startup_fails_without_a_usable_root(environment: dict[str, str]) -> None:
    """A missing or unusable root stops the process before it serves anything."""
    result = _start(environment)
    assert result.returncode != 0
    assert result.stdout == ""
    assert "KNOWLEDGE_ROOT" in result.stderr


def test_startup_fails_without_ripgrep(vault: Path, tmp_path: Path) -> None:
    """The server does not start when `rg` is not on PATH."""
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    result = _start({"KNOWLEDGE_ROOT": str(vault), "PATH": str(empty_bin)})
    assert result.returncode != 0
    assert result.stdout == ""
    assert "ripgrep" in result.stderr
    assert str(vault) not in result.stderr
