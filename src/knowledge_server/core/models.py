"""Request and result models for the four tools, and the domain errors.

The models are strict: they reject unknown fields and do not convert values,
so `"5"` is not accepted where an integer is required. Field descriptions are
written for tool callers, so that tool schemas built from these models can
show them.
"""

import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from knowledge_server.core.limits import DEFAULT_LIMITS


class DomainErrorCode(StrEnum):
    """Error codes that the contract exposes to tool callers."""

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


# Fixed messages keep file paths, note text, and exception details out of
# responses: an error message never includes the input that caused it.
# `invalid_argument_message` replaces the INVALID_ARGUMENT message with one
# that names the rejected arguments, still without their values.
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
    """A domain error with a contract code and a fixed, safe message.

    Attributes:
        code: The contract error code.
        message: A message for the code that contains no request data.
    """

    def __init__(self, code: DomainErrorCode, message: str | None = None) -> None:
        """Create an error for a contract code.

        Args:
            code: The contract error code to report.
            message: A safe message to use instead of the code's fixed one.
        """
        self.code = code
        self.message = _SAFE_MESSAGES[code] if message is None else message
        super().__init__(self.message)


def invalid_argument_message(model: type[BaseModel], error: ValidationError) -> str:
    """Describe rejected arguments without repeating any supplied value.

    Each rejected argument is named with the values its schema accepts, so the
    message follows the limits. An unknown argument is not named, because the
    caller chose the name; the message lists the valid arguments instead.

    Args:
        model: The request model that rejected the arguments.
        error: The error raised by validating the arguments.

    Returns:
        One sentence per problem, joined by spaces.
    """
    properties = model.model_json_schema()["properties"]
    sentences: list[str] = []
    for problem in error.errors():
        location = problem["loc"]
        if problem["type"] == "extra_forbidden":
            sentence = f"Unknown argument. Valid arguments: {', '.join(properties)}."
        elif problem["type"] == "value_error" and not location and "ctx" in problem:
            # Raised by this module's model validators with fixed text.
            sentence = str(problem["ctx"]["error"])
        elif location and location[0] in properties:
            name = location[0]
            required = " is required and" if problem["type"] == "missing" else ""
            sentence = f"{name}{required} must be {_accepted_values(properties[name])}."
        else:
            sentence = _SAFE_MESSAGES[DomainErrorCode.INVALID_ARGUMENT]
        sentences.append(sentence)
    return " ".join(dict.fromkeys(sentences))


def _accepted_values(schema: dict[str, Any]) -> str:
    options = schema.get("anyOf", [schema])
    value = next(option for option in options if option.get("type") != "null")
    if "const" in value:
        text = json.dumps(value["const"])
    elif value["type"] == "integer":
        text = "an integer"
        if "maximum" in value:
            text += f" from {value['minimum']} to {value['maximum']}"
        elif "minimum" in value:
            text += f" of at least {value['minimum']}"
    elif value["type"] == "string" and "maxLength" in value:
        text = f"a string of {value['minLength']} to {value['maxLength']} characters"
    elif value["type"] == "string":
        text = "a string"
    elif value["type"] == "boolean":
        text = "true or false"
    else:
        text = f"a valid {value['type']}"
    return text + (" or null" if len(options) > 1 else "")


class ContractModel(BaseModel):
    """Base model that rejects unknown fields and all type conversion."""

    model_config = ConfigDict(extra="forbid", strict=True)


StrictPositiveInt = Annotated[int, Field(strict=True, ge=1)]
StrictNonNegativeInt = Annotated[int, Field(strict=True, ge=0)]

_PATH_DESCRIPTION = "Path relative to the vault root, using '/' as separator."


class SearchRequest(ContractModel):
    """Arguments for literal search."""

    query: str = Field(
        strict=True,
        min_length=1,
        max_length=DEFAULT_LIMITS.max_query_length,
        description=(
            "Literal text to find within one line, at most "
            f"{DEFAULT_LIMITS.max_query_length} characters. Spaces are "
            "significant; there is no regular expression or query syntax."
        ),
    )
    path: str = Field(
        default="",
        strict=True,
        description=(
            "Note or directory to search, relative to the vault root. "
            "Empty searches the whole vault; directories are searched "
            "recursively."
        ),
    )
    max_results: Annotated[
        int, Field(strict=True, ge=1, le=DEFAULT_LIMITS.max_search_results)
    ] = Field(
        default=DEFAULT_LIMITS.default_search_results,
        description=(
            f"Most matches to return, from 1 to {DEFAULT_LIMITS.max_search_results}."
        ),
    )
    case_sensitive: bool = Field(
        default=False,
        strict=True,
        description="Match letter case exactly when true.",
    )
    mode: Literal["literal"] = Field(
        default="literal",
        description="Search mode. Only literal matching is available.",
    )

    @model_validator(mode="after")
    def validate_literal_query(self) -> SearchRequest:
        """Reject a blank or multi-line query without trimming its spaces.

        Returns:
            The unchanged request.

        Raises:
            ValueError: If the query is only whitespace or contains a line
                break or NUL character.
        """
        if (
            not self.query.strip()
            or "\n" in self.query
            or "\r" in self.query
            or "\x00" in self.query
        ):
            raise ValueError(
                "query must contain non-space text on a single line, without NUL "
                "characters."
            )
        return self


class ReadRequest(ContractModel):
    """Arguments for reading a range of lines from one note."""

    path: str = Field(strict=True, description=f"Note to read. {_PATH_DESCRIPTION}")
    start_line: StrictPositiveInt = Field(
        default=1, description="First line to return. Line numbers start at 1."
    )
    end_line: StrictPositiveInt | None = Field(
        default=None,
        description=(
            "Last line to return, inclusive. Null requests the default number "
            "of lines. The range can be at most "
            f"{DEFAULT_LIMITS.max_read_lines} lines; a range beyond the end of "
            "the note is shortened."
        ),
    )

    @model_validator(mode="after")
    def validate_range(self) -> ReadRequest:
        """Check that an explicit range is ordered and within the maximum.

        Returns:
            The unchanged request.

        Raises:
            ValueError: If `end_line` is before `start_line` or the range is
                longer than the maximum read range.
        """
        if self.end_line is not None and (
            self.end_line < self.start_line
            or self.end_line - self.start_line + 1 > DEFAULT_LIMITS.max_read_lines
        ):
            raise ValueError(
                "end_line must not be before start_line, and the range must be "
                f"at most {DEFAULT_LIMITS.max_read_lines} lines."
            )
        return self


class ListRequest(ContractModel):
    """Arguments for listing one page of a directory's immediate children."""

    path: str = Field(
        default="",
        strict=True,
        description=f"Directory to list; empty lists the root. {_PATH_DESCRIPTION}",
    )
    offset: StrictNonNegativeInt = Field(
        default=0,
        description=(
            "Number of entries to skip, 0 or more. Pass the previous result's "
            "`next_offset` to get the next page."
        ),
    )
    limit: Annotated[
        int, Field(strict=True, ge=1, le=DEFAULT_LIMITS.max_directory_page)
    ] = Field(
        default=DEFAULT_LIMITS.default_directory_page,
        description=(
            "Most entries to return in this page, from 1 to "
            f"{DEFAULT_LIMITS.max_directory_page}."
        ),
    )


class InfoRequest(ContractModel):
    """Arguments for metadata about one note."""

    path: str = Field(strict=True, description=f"Note to inspect. {_PATH_DESCRIPTION}")


class SearchMatch(ContractModel):
    """One line that matches a search query."""

    path: str = Field(description="Matching note, relative to the vault root.")
    line: StrictPositiveInt = Field(description="Line number of the match.")
    snippet: str = Field(description="Part of the line around the first match.")
    snippet_truncated: bool = Field(
        description="True when the snippet is shorter than the whole line."
    )


class SearchSkipped(ContractModel):
    """Counts of notes that a recursive search could not search."""

    too_large: StrictNonNegativeInt = Field(
        default=0, description="Notes larger than the file-size limit."
    )
    invalid_text: StrictNonNegativeInt = Field(
        default=0, description="Notes that are not valid UTF-8 or contain NUL bytes."
    )
    unreadable: StrictNonNegativeInt = Field(
        default=0,
        description="Notes that could not be opened, or disappeared first.",
    )


class SearchResult(ContractModel):
    """Result of a literal search."""

    mode: Literal["literal"] = Field(
        default="literal", description="Search mode that was used."
    )
    matches: list[SearchMatch] = Field(
        default_factory=list,
        description="Matches ordered by path and then line number.",
    )
    truncated: bool = Field(
        default=False,
        description="True when more matches exist than were returned.",
    )
    incomplete: bool = Field(
        default=False,
        description="True when some notes were skipped; see `skipped`.",
    )
    skipped: SearchSkipped = Field(
        default_factory=SearchSkipped,
        description="Counts of skipped notes by reason.",
    )


class ReadResult(ContractModel):
    """Result of reading a range of lines from one note."""

    path: str = Field(description="Note that was read, relative to the vault root.")
    numbered_content: str = Field(
        description=(
            "Returned lines. Each line is its line number, a tab, the line "
            "text, and a line feed, so a citation can use the number directly. "
            "Consecutive pages can be joined directly."
        )
    )
    start_line: StrictPositiveInt | None = Field(
        description="First returned line, or null if no lines were returned."
    )
    end_line: StrictPositiveInt | None = Field(
        description="Last returned line, or null if no lines were returned."
    )
    total_lines: StrictNonNegativeInt = Field(
        description="Number of lines in the whole note."
    )
    next_line: StrictPositiveInt | None = Field(
        description=(
            "First line after `end_line`, to continue reading; null at the end "
            "of the note."
        )
    )
    truncated: bool = Field(
        description=(
            "True when the content size limit stopped the read before the end "
            "of the requested range."
        )
    )
    content_sha256: str = Field(
        description=(
            "SHA-256 of the note's file bytes as loaded, to detect changes "
            "between calls."
        )
    )


class ListEntry(ContractModel):
    """One visible immediate child of a listed directory."""

    path: str = Field(description="Entry path, relative to the vault root.")
    kind: Literal["file", "directory"] = Field(
        description="Whether the entry is a note or a directory."
    )


class ListResult(ContractModel):
    """Result of listing one page of a directory."""

    path: str = Field(
        description="Listed directory, relative to the vault root; empty for the root."
    )
    entries: list[ListEntry] = Field(
        description="Visible entries in this page, ordered by path."
    )
    next_offset: StrictNonNegativeInt | None = Field(
        description=(
            "Offset to request the next page, or null when this page is the last."
        )
    )
    truncated: bool = Field(description="True when more entries follow this page.")


class InfoResult(ContractModel):
    """Metadata about one note, including notes too large or invalid to read."""

    path: str = Field(description="Note path, relative to the vault root.")
    size_bytes: StrictNonNegativeInt = Field(description="File size in bytes.")
    modified_at: str = Field(
        description="Last modification time from the filesystem, in UTC (RFC 3339)."
    )
    line_count: StrictNonNegativeInt | None = Field(
        description="Number of lines, or null when the note is not readable."
    )
    content_sha256: str | None = Field(
        description=(
            "SHA-256 of the file bytes, or null when the note is not readable."
        )
    )
    readable: bool = Field(description="True when read and search can use the note.")
    unreadable_reason: Literal["too_large", "invalid_text"] | None = Field(
        default=None,
        description="Why the note is not readable, or null when it is readable.",
    )
