"""The knowledge tools, registered on an MCP SDK server.

The three read tools are always served. The proposal tools, which use
unreviewed branches, are served only when a vault remote is configured; the
stdio entry point never does.

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

from knowledge_server.core.git import SSH_COMMAND, ProposalBranches
from knowledge_server.core.limits import DEFAULT_LIMITS, Limits
from knowledge_server.core.models import (
    DomainErrorCode,
    KnowledgeError,
    ListProposalsRequest,
    ListProposalsResult,
    ListRequest,
    ListResult,
    ProposalReadRequest,
    ProposalResult,
    ProposeEditRequest,
    ProposeNoteRequest,
    ReadRequest,
    ReadResult,
    SearchRequest,
    SearchResult,
    invalid_argument_message,
)
from knowledge_server.core.paths import PathPolicy
from knowledge_server.core.reader import list_directory, read_note
from knowledge_server.core.search import search_notes
from knowledge_server.core.writer import propose_edit, propose_note

_logger = logging.getLogger(__name__)
# The HTTP log filter recognizes this record by its format string.
TOOL_FAILURE_FORMAT = "%s failed with %s%s"

_WRITE_GUIDANCE = (
    " Before proposing, read AGENTS.md at the root with knowledge_read, if it "
    "exists, and follow its conventions for notes. Where it gives shell "
    "commands, search with the knowledge tools instead; skip Git steps and "
    "steps that run scripts. In note text, refer to other notes by their vault "
    "path."
)
_SEARCH_DESCRIPTION = (
    "Find lines in the Markdown notes of the knowledge vault that contain any "
    f"of up to {DEFAULT_LIMITS.max_search_queries} literal texts. Put "
    "alternative wordings, such as synonyms and translations, in one call. "
    "Returns one match per line with its note path, line number, and a "
    "snippet, ordered by path and line; max_results limits all queries "
    "together. Matching ignores letter case unless case_sensitive is true. "
    "There is no regular expression, word splitting, or relevance ranking; "
    "narrow the path or queries when the result is truncated. Use "
    "knowledge_read to read the lines around a match."
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
_PROPOSE_NOTE_DESCRIPTION = (
    "Save a new Markdown note as a proposal on an unreviewed branch of the "
    "knowledge vault. The note becomes part of the vault only after the user "
    "reviews and merges the branch. path is the vault path the note should "
    "have, such as Projects/Plan.md; no note may exist there. Pass topic, a "
    "short name for the change, to start a new proposal branch, or pass "
    "proposal, a branch name that an earlier write or knowledge_list_proposals "
    "returned, to add the note to that branch." + _WRITE_GUIDANCE
)
_PROPOSE_EDIT_DESCRIPTION = (
    "Propose exact text replacements in a note of the knowledge vault, on an "
    "unreviewed proposal branch. The note changes only after the user reviews "
    "and merges the branch. Pass topic, a short name for the change, to start "
    "a new proposal branch, or pass proposal, a branch name that an earlier "
    "write or knowledge_list_proposals returned, to revise that branch. Read "
    "the note first, with the same proposal for a revision, and pass its "
    "content_sha256 as base_sha256. Each old text must occur exactly once in "
    "the text that knowledge_read shows, without line numbers; if it occurs "
    "more than once, include surrounding lines until it is unique. The edits "
    "apply in order, all or nothing. Call this only when the user explicitly "
    "asks to change a note and has agreed to the change, never on your own "
    "initiative." + _WRITE_GUIDANCE
)


def create_server(
    policy: PathPolicy,
    *,
    ripgrep: str,
    limits: Limits = DEFAULT_LIMITS,
    vault_remote: str | None = None,
    state_directory: Path | None = None,
    ssh_command: str | None = SSH_COMMAND,
) -> MCPServer:
    """Create a server that serves the knowledge tools.

    Blocking reads and writes run in a worker thread so that they do not
    block the protocol loop. Search is awaited directly, so cancelling a
    request cancels the search and kills its ripgrep process.

    Args:
        policy: The policy for the vault root.
        ripgrep: Path of the ripgrep executable.
        limits: Limits for every operation.
        vault_remote: Vault remote URL; configuring it enables proposal tools.
        state_directory: Internal state directory; None uses STATE_DIRECTORY.
        ssh_command: Remote SSH command; None disables it for local tests.

    Returns:
        A server that has not started yet.
    """
    proposal_branches = (
        ProposalBranches(
            policy,
            vault_remote,
            state_directory=state_directory,
            limits=limits,
            ssh_command=ssh_command,
        )
        if vault_remote is not None
        else None
    )

    async def search(request: SearchRequest) -> SearchResult:
        return await search_notes(policy, request, limits, ripgrep=ripgrep)

    async def read(request: ReadRequest) -> ReadResult:
        if isinstance(request, ProposalReadRequest) and request.proposal is not None:
            assert proposal_branches is not None
            return await asyncio.to_thread(proposal_branches.read, request)
        return await asyncio.to_thread(read_note, policy, request, limits)

    async def list_(request: ListRequest) -> ListResult:
        return await asyncio.to_thread(list_directory, policy, request, limits)

    async def note(request: ProposeNoteRequest) -> ProposalResult:
        assert proposal_branches is not None
        return await asyncio.to_thread(propose_note, proposal_branches, request, limits)

    async def edit(request: ProposeEditRequest) -> ProposalResult:
        assert proposal_branches is not None
        return await asyncio.to_thread(propose_edit, proposal_branches, request, limits)

    async def proposals(request: ListProposalsRequest) -> ListProposalsResult:
        assert proposal_branches is not None
        return await asyncio.to_thread(proposal_branches.list_proposals)

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
            _READ_DESCRIPTION
            + (
                " Pass proposal, a proposal branch name, to read the note's "
                "unreviewed version on that branch."
                if vault_remote is not None
                else ""
            ),
            ProposalReadRequest if vault_remote is not None else ReadRequest,
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
    ]
    if vault_remote is not None:
        tools += [
            _tool(
                "knowledge_list_proposals",
                "List proposals",
                "List the unreviewed proposal branches of the knowledge vault "
                "and the notes that each one changes. Pass a branch name as "
                "proposal to knowledge_read to read its version of a note, or "
                "to a write tool to revise the branch.",
                ListProposalsRequest,
                ListProposalsResult,
                proposals,
            ),
            _tool(
                "knowledge_propose_note",
                "Propose note",
                _PROPOSE_NOTE_DESCRIPTION,
                ProposeNoteRequest,
                ProposalResult,
                note,
                destructive=False,
            ),
            _tool(
                "knowledge_propose_edit",
                "Propose edit",
                _PROPOSE_EDIT_DESCRIPTION,
                ProposeEditRequest,
                ProposalResult,
                edit,
                destructive=True,
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
    *,
    destructive: bool | None = None,
) -> Tool:
    """Build a tool that validates arguments and translates errors.

    Without `destructive`, the tool is annotated as read-only and idempotent.
    A write tool passes `destructive` and is annotated as neither.
    """

    async def run(arguments: dict[str, Any]) -> ResultT | CallToolResult:
        try:
            try:
                request = request_model.model_validate(arguments)
            except ValidationError as error:
                message = invalid_argument_message(request_model, error)
                return _error_result(
                    KnowledgeError(DomainErrorCode.INVALID_ARGUMENT, message)
                )
            return await operation(request)
        except KnowledgeError as error:
            return _error_result(error)
        # Every other failure becomes a safe INTERNAL_ERROR; otherwise the SDK
        # would log the exception's text. Catch Exception, not BaseException,
        # so that cancellation still propagates.
        except Exception as error:  # noqa: BLE001
            _log_unexpected(name, error)
            return _error_result(KnowledgeError(DomainErrorCode.INTERNAL_ERROR))

    return Tool(
        fn=run,
        name=name,
        title=title,
        description=description,
        parameters=_tool_schema(request_model),
        fn_metadata=FuncMetadata(
            arg_model=_RawArguments,
            output_model=result_model,
            output_schema=_tool_schema(result_model),
        ),
        is_async=True,
        context_kwarg=None,
        annotations=ToolAnnotations(
            title=title,
            read_only_hint=destructive is None,
            destructive_hint=bool(destructive),
            idempotent_hint=destructive is None,
            open_world_hint=False,
        ),
    )


def _tool_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Remove generated model labels while retaining field documentation."""
    schema = model.model_json_schema()

    def clean(value: Any, *, model_schema: bool = False) -> None:
        if isinstance(value, dict):
            value.pop("title", None)
            if model_schema:
                value.pop("description", None)
            for key, child in value.items():
                if key == "$defs":
                    for definition in child.values():
                        clean(definition, model_schema=True)
                else:
                    clean(child)
        elif isinstance(value, list):
            for child in value:
                clean(child)

    clean(schema, model_schema=True)
    return schema


def _error_result(error: KnowledgeError) -> CallToolResult:
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
    _logger.error(TOOL_FAILURE_FORMAT, tool, type(error).__name__, location)
