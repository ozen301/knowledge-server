"""Strict request and result models shared by future MCP tool wrappers."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from knowledge_server.core.limits import DEFAULT_LIMITS


class DomainErrorCode(StrEnum):
    """Safe domain failures exposed by the server contract."""

    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    INVALID_PATH = "INVALID_PATH"
    NOT_FOUND = "NOT_FOUND"
    ACCESS_DENIED = "ACCESS_DENIED"
    UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"
    NOT_A_FILE = "NOT_A_FILE"
    NOT_A_DIRECTORY = "NOT_A_DIRECTORY"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    INVALID_ENCODING = "INVALID_ENCODING"
    LINE_TOO_LONG = "LINE_TOO_LONG"
    SEARCH_LIMIT_EXCEEDED = "SEARCH_LIMIT_EXCEEDED"
    DIRECTORY_LIMIT_EXCEEDED = "DIRECTORY_LIMIT_EXCEEDED"
    SEARCH_FAILED = "SEARCH_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_SAFE_MESSAGES: dict[DomainErrorCode, str] = {
    DomainErrorCode.INVALID_ARGUMENT: "The request arguments are invalid.",
    DomainErrorCode.INVALID_PATH: "The requested path is invalid.",
    DomainErrorCode.NOT_FOUND: "The requested item was not found.",
    DomainErrorCode.ACCESS_DENIED: "Access to the requested item is denied.",
    DomainErrorCode.UNSUPPORTED_TYPE: "The requested file type is not supported.",
    DomainErrorCode.NOT_A_FILE: "The requested item is not a file.",
    DomainErrorCode.NOT_A_DIRECTORY: "The requested item is not a directory.",
    DomainErrorCode.FILE_TOO_LARGE: "The requested file exceeds the size limit.",
    DomainErrorCode.INVALID_ENCODING: "The requested file is not valid text.",
    DomainErrorCode.LINE_TOO_LONG: "The requested line exceeds the content limit.",
    DomainErrorCode.SEARCH_LIMIT_EXCEEDED: "The search exceeded a resource limit.",
    DomainErrorCode.DIRECTORY_LIMIT_EXCEEDED: "The directory exceeds the entry limit.",
    DomainErrorCode.SEARCH_FAILED: "The search could not be completed.",
    DomainErrorCode.INTERNAL_ERROR: "An internal server error occurred.",
}


class KnowledgeError(Exception):
    """A domain error with a contract code and a generic safe message."""

    def __init__(self, code: DomainErrorCode) -> None:
        self.code = code
        self.message = _SAFE_MESSAGES[code]
        super().__init__(self.message)


class ContractModel(BaseModel):
    """Base model that rejects unknown fields and all coercion."""

    model_config = ConfigDict(extra="forbid", strict=True)


StrictPositiveInt = Annotated[int, Field(strict=True, ge=1)]
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]


class SearchRequest(ContractModel):
    """Arguments for literal search."""

    query: str = Field(
        strict=True, min_length=1, max_length=DEFAULT_LIMITS.max_query_length
    )
    path: str = Field(default="", strict=True)
    max_results: Annotated[
        int, Field(strict=True, ge=1, le=DEFAULT_LIMITS.max_search_results)
    ] = DEFAULT_LIMITS.default_search_results
    case_sensitive: bool = Field(default=False, strict=True)
    mode: Literal["literal"] = "literal"

    @model_validator(mode="after")
    def validate_literal_query(self) -> SearchRequest:
        """Reject blank or multi-line values without changing significant spaces."""
        if (
            not self.query.strip()
            or "\n" in self.query
            or "\r" in self.query
            or "\x00" in self.query
        ):
            raise ValueError("query must be a non-blank single line")
        return self


class ReadRequest(ContractModel):
    """Arguments for bounded line-oriented reading."""

    path: str = Field(strict=True)
    start_line: StrictPositiveInt = 1
    end_line: StrictPositiveInt | None = None

    @model_validator(mode="after")
    def validate_range(self) -> ReadRequest:
        """Ensure an explicit inclusive range is ordered and within the limit."""
        if self.end_line is not None and (
            self.end_line < self.start_line
            or self.end_line - self.start_line + 1 > DEFAULT_LIMITS.max_read_lines
        ):
            raise ValueError("read range is invalid")
        return self


class ListRequest(ContractModel):
    """Arguments for immediate-directory listing."""

    path: str = Field(default="", strict=True)
    offset: StrictNonNegativeInt = 0
    limit: Annotated[
        int, Field(strict=True, ge=1, le=DEFAULT_LIMITS.max_directory_page)
    ] = DEFAULT_LIMITS.default_directory_page


class InfoRequest(ContractModel):
    """Arguments for visible Markdown file metadata."""

    path: str = Field(strict=True)


class SearchMatch(ContractModel):
    """One literal-search hit at a citable line."""

    path: str
    line: StrictPositiveInt
    snippet: str
    snippet_truncated: bool


class SearchSkipped(ContractModel):
    """Counts of eligible files skipped while recursively searching."""

    too_large: StrictNonNegativeInt = 0
    invalid_text: StrictNonNegativeInt = 0
    unreadable: StrictNonNegativeInt = 0


class SearchResult(ContractModel):
    """Structured result for literal search."""

    mode: Literal["literal"] = "literal"
    matches: list[SearchMatch] = Field(default_factory=list)
    truncated: bool = False
    incomplete: bool = False
    skipped: SearchSkipped = Field(default_factory=SearchSkipped)


class ReadResult(ContractModel):
    """Structured result for a bounded note read."""

    path: str
    content: str
    start_line: StrictPositiveInt | None
    end_line: StrictPositiveInt | None
    total_lines: StrictNonNegativeInt
    next_line: StrictPositiveInt | None
    truncated: bool
    content_sha256: str


class ListEntry(ContractModel):
    """One visible immediate child in a directory listing."""

    path: str
    kind: Literal["file", "directory"]


class ListResult(ContractModel):
    """Structured result for immediate-directory listing."""

    path: str
    entries: list[ListEntry]
    next_offset: StrictNonNegativeInt | None
    truncated: bool


class InfoResult(ContractModel):
    """Structured result for metadata about a visible Markdown file."""

    path: str
    size_bytes: StrictNonNegativeInt
    modified_at: str
    line_count: StrictNonNegativeInt | None
    content_sha256: str | None
    readable: bool
    unreadable_reason: Literal["too_large", "invalid_text"] | None = None
