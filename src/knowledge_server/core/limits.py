"""Resource limits shared by request validation and the core operations.

Each limit is defined once here. Request models use `DEFAULT_LIMITS` to reject
out-of-range arguments, and the core operations accept a `Limits` value so
tests can inject smaller limits instead of building large fixtures.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Limits:
    """The resource limits defined by the tool contract.

    Byte limits count UTF-8 or raw file bytes as stated; length limits count
    Unicode code points.

    Attributes:
        max_file_bytes: Largest file, in raw bytes, that read and search load.
            Larger files can still be listed and inspected with info.
        max_query_length: Longest search query, in code points.
        default_search_results: Number of search hits returned when the caller
            does not set `max_results`.
        max_search_results: Largest `max_results` a caller may request.
        max_snippet_length: Longest snippet in a search hit, in code points.
        default_read_lines: Number of lines a read returns when the caller does
            not set `end_line`.
        max_read_lines: Largest line range one read request may ask for.
        max_read_content_bytes: Most line text one read returns, in UTF-8
            bytes after newline normalization and without the line-number
            prefixes. A read stops before a line that would exceed it.
        default_directory_page: Number of entries a listing returns when the
            caller does not set `limit`.
        max_directory_page: Largest `limit`, that is, the most entries one
            listing call may return. Callers page through larger directories
            with `offset`.
        search_deadline_seconds: Time limit for a whole search operation.
        max_search_entries: Most filesystem entries one search may inspect,
            counted before visibility filtering.
        max_search_source_bytes: Most raw file bytes one search may load while
            checking candidate files.
        max_search_output_bytes: Most bytes of ripgrep output (stdout and
            stderr together) one search may read.
        max_immediate_directory_entries: Most entries one listing may scan in
            a single directory before it fails with `DIRECTORY_LIMIT_EXCEEDED`.
    """

    max_file_bytes: int = 1 * 1024 * 1024
    max_query_length: int = 512
    default_search_results: int = 20
    max_search_results: int = 50
    max_snippet_length: int = 300
    default_read_lines: int = 200
    max_read_lines: int = 200
    max_read_content_bytes: int = 32 * 1024
    default_directory_page: int = 100
    max_directory_page: int = 200
    search_deadline_seconds: float = 10.0
    max_search_entries: int = 10_000
    max_search_source_bytes: int = 16 * 1024 * 1024
    max_search_output_bytes: int = 4 * 1024 * 1024
    max_immediate_directory_entries: int = 10_000


DEFAULT_LIMITS = Limits()
"""The limits the server enforces unless a caller injects others."""
