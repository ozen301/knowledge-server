"""Initial Phase 1 resource limits shared by models and core operations."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Limits:
    """The bounded-resource settings defined by the Phase 1 contract."""

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
