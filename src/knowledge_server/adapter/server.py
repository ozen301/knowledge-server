"""The four knowledge tools, registered on an MCP SDK server.

Each tool validates its arguments with the request model, calls one core
operation, and returns the result model, which the SDK sends as structured
content with an equivalent JSON text item. Failures become tool results with
`isError=true` whose text is a JSON object with the error `code` and a fixed
`message`. The core decides policy and errors; this module only translates.
"""

import asyncio
import importlib.metadata
import json
import logging
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

# The SDK does not export these classes. Building `Tool` objects directly is
# the only way to publish the request models' schemas and still receive the
# raw arguments, so that the strict models, not the SDK's lenient argument
# model, validate them. Check this module when upgrading the SDK.
from mcp.server.mcpserver.tools.base import Tool
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase, FuncMetadata
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, ValidationError

from knowledge_server.core.limits import DEFAULT_LIMITS, Limits
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
from knowledge_server.core.search import search_notes

_logger = logging.getLogger(__name__)

_SEARCH_DESCRIPTION = (
    "Find lines in the Markdown notes of the knowledge vault that contain a "
    "literal text. Returns one match per line with its note path, line "
    "number, and a snippet, ordered by path and line. Matching ignores letter "
    "case unless case_sensitive is true. There is no regular expression, "
    "word splitting, or relevance ranking; narrow the path or query when the "
    "result is truncated. Use knowledge_read to read the lines around a match."
)
_READ_DESCRIPTION = (
    "Read a range of whole lines from one Markdown note in the knowledge "
    "vault. Line numbers start at 1. Returns the lines, the actual range, the "
    "total line count, and next_line for continuing a long note."
)
_LIST_DESCRIPTION = (
    "List one page of the visible notes and subdirectories directly inside a "
    "directory of the knowledge vault. An empty path lists the root. Use "
    "next_offset to get the next page."
)
_INFO_DESCRIPTION = (
    "Return metadata for one Markdown note in the knowledge vault: size, "
    "modification time, line count, content hash, and whether the note can "
    "be read and searched."
)


def create_server(
    policy: PathPolicy, *, ripgrep: str, limits: Limits = DEFAULT_LIMITS
) -> MCPServer:
    """Create a server that serves the four knowledge tools.

    Blocking reads run in a worker thread so that they do not block the
    protocol loop. Search is awaited directly, so cancelling a request
    cancels the search and kills its ripgrep process.

    Args:
        policy: The policy for the vault root.
        ripgrep: Path of the ripgrep executable.
        limits: Limits for every operation.

    Returns:
        A server that has not started yet.
    """

    async def search(request: SearchRequest) -> SearchResult:
        return await search_notes(policy, request, limits, ripgrep=ripgrep)

    async def read(request: ReadRequest) -> ReadResult:
        return await asyncio.to_thread(read_note, policy, request, limits)

    async def list_(request: ListRequest) -> ListResult:
        return await asyncio.to_thread(list_directory, policy, request, limits)

    async def info(request: InfoRequest) -> InfoResult:
        return await asyncio.to_thread(note_info, policy, request, limits)

    tools = [
        _tool(
            "knowledge_search",
            "Search notes",
            _SEARCH_DESCRIPTION,
            SearchRequest,
            SearchResult,
            search,
        ),
        _tool(
            "knowledge_read",
            "Read note",
            _READ_DESCRIPTION,
            ReadRequest,
            ReadResult,
            read,
        ),
        _tool(
            "knowledge_list",
            "List directory",
            _LIST_DESCRIPTION,
            ListRequest,
            ListResult,
            list_,
        ),
        _tool(
            "knowledge_info",
            "Note info",
            _INFO_DESCRIPTION,
            InfoRequest,
            InfoResult,
            info,
        ),
    ]
    return MCPServer(
        name="knowledge-server",
        version=importlib.metadata.version("knowledge-server"),
        tools=tools,
    )


class _RawArguments(ArgModelBase):
    """Pass a call's arguments to the tool function unchanged.

    The model declares no fields, so the SDK neither converts JSON strings
    nor validates anything; the request models do all validation.
    """

    model_config = ConfigDict(extra="allow")

    def model_dump_one_level(self) -> dict[str, Any]:
        """Return the arguments as the single `arguments` keyword argument.

        Returns:
            A mapping from `arguments` to the call's argument object.
        """
        return {"arguments": dict(self.model_extra or {})}


def _tool[RequestT: BaseModel, ResultT: BaseModel](
    name: str,
    title: str,
    description: str,
    request_model: type[RequestT],
    result_model: type[ResultT],
    operation: Callable[[RequestT], Awaitable[ResultT]],
) -> Tool:
    """Build a tool that validates arguments and translates errors."""

    async def run(arguments: dict[str, Any]) -> ResultT | CallToolResult:
        try:
            try:
                request = request_model.model_validate(arguments)
            except ValidationError:
                return _error_result(DomainErrorCode.INVALID_ARGUMENT)
            return await operation(request)
        except KnowledgeError as error:
            return _error_result(error.code)
        # Every other failure becomes a safe INTERNAL_ERROR; otherwise the SDK
        # would log the exception's text. Catch Exception, not BaseException,
        # so that cancellation still propagates.
        except Exception as error:  # noqa: BLE001
            _log_unexpected(name, error)
            return _error_result(DomainErrorCode.INTERNAL_ERROR)

    return Tool(
        fn=run,
        name=name,
        title=title,
        description=description,
        parameters=request_model.model_json_schema(),
        fn_metadata=FuncMetadata(arg_model=_RawArguments, output_model=result_model),
        is_async=True,
        context_kwarg=None,
        annotations=ToolAnnotations(
            title=title,
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )


def _error_result(code: DomainErrorCode) -> CallToolResult:
    error = KnowledgeError(code)
    text = json.dumps({"code": error.code.value, "message": error.message})
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def _log_unexpected(tool: str, error: Exception) -> None:
    """Log where an unexpected exception was raised, without its message.

    Exception messages can contain note text, queries, or file paths, so the
    log names only the exception type and the source location.
    """
    frames = traceback.extract_tb(error.__traceback__)
    location = ""
    if frames:
        frame = frames[-1]
        location = f" at {Path(frame.filename).name}:{frame.lineno} in {frame.name}"
    _logger.error("%s failed with %s%s", tool, type(error).__name__, location)
