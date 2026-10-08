"""Tests for strict request and reusable result models."""

import pytest
from pydantic import ValidationError

from knowledge_server.core.models import (
    InfoRequest,
    InfoResult,
    ListEntry,
    ListRequest,
    ListResult,
    ReadRequest,
    ReadResult,
    SearchMatch,
    SearchRequest,
    SearchResult,
)


@pytest.mark.parametrize("value", [True, "20", 20.0, 0, 51])
def test_search_result_limit_is_a_strict_bounded_integer(value: object) -> None:
    """Search limits reject coercion and values outside the contract bounds."""
    with pytest.raises(ValidationError):
        SearchRequest(queries=["needle"], max_results=value)  # type: ignore[arg-type]


def test_requests_apply_contract_defaults() -> None:
    """Future tool wrappers can use the shared request defaults unchanged."""
    assert SearchRequest(queries=["needle"]).model_dump() == {
        "queries": ["needle"],
        "path": "",
        "max_results": 20,
        "case_sensitive": False,
        "mode": "literal",
    }
    assert ListRequest().model_dump() == {"path": "", "offset": 0, "limit": 100}
    assert ReadRequest(path="Notes/a.md").model_dump() == {
        "path": "Notes/a.md",
        "start_line": 1,
        "end_line": None,
    }


def test_request_boundaries_are_accepted_without_coercion() -> None:
    """Every numeric request field accepts its exact documented boundaries."""
    assert SearchRequest(queries=[" preserved spaces "], max_results=1).queries == [
        " preserved spaces "
    ]
    assert (
        SearchRequest(
            queries=["needle"], max_results=50, case_sensitive=True
        ).max_results
        == 50
    )
    assert SearchRequest(queries=["x" * 512]).queries == ["x" * 512]
    assert SearchRequest(queries=["a", "b", "c", "d", "a"]).queries == [
        "a",
        "b",
        "c",
        "d",
        "a",
    ]
    assert ReadRequest(path="Notes/a.md", start_line=1, end_line=200).end_line == 200
    assert ListRequest(offset=0, limit=1).limit == 1
    assert ListRequest(offset=9, limit=200).offset == 9


@pytest.mark.parametrize(
    ("model", "arguments"),
    [
        (SearchRequest, {"queries": ["x" * 513]}),
        (SearchRequest, {"queries": ["needle", "x" * 513]}),
        (SearchRequest, {"queries": []}),
        (SearchRequest, {"queries": ["a", "b", "c", "d", "e", "f"]}),
        (ReadRequest, {"path": "a.md", "start_line": 1, "end_line": 201}),
        (ListRequest, {"limit": 0}),
        (ListRequest, {"limit": 201}),
        (ListRequest, {"offset": -1}),
    ],
)
def test_request_limit_boundaries_reject_values_beyond_the_contract(
    model: type[SearchRequest | ReadRequest | ListRequest], arguments: dict[str, object]
) -> None:
    """Contract maxima and minima reject requests just beyond their boundaries."""
    with pytest.raises(ValidationError):
        model.model_validate(arguments)


@pytest.mark.parametrize(
    ("model", "arguments"),
    [
        (SearchRequest, {"queries": ["needle"], "case_sensitive": 1}),
        (SearchRequest, {"queries": "needle"}),
        (SearchRequest, {"queries": ("needle",)}),
        (SearchRequest, {"queries": ["needle", 1]}),
        (SearchRequest, {"query": "needle"}),
        (ReadRequest, {"path": "a.md", "start_line": True}),
        (ReadRequest, {"path": "a.md", "end_line": "2"}),
        (ListRequest, {"offset": False}),
        (ListRequest, {"limit": "1"}),
        (SearchRequest, {"queries": ["needle"], "root": "/outside"}),
        (ReadRequest, {"path": "a.md", "root": "/outside"}),
        (ListRequest, {"root": "/outside"}),
        (InfoRequest, {"path": "a.md", "root": "/outside"}),
    ],
)
def test_requests_reject_coercion_and_root_overrides(
    model: type[SearchRequest | ReadRequest | ListRequest | InfoRequest],
    arguments: dict[str, object],
) -> None:
    """Tool arguments cannot coerce values or override configured configuration."""
    with pytest.raises(ValidationError):
        model.model_validate(arguments)


@pytest.mark.parametrize("query", ["", " \t ", "one\ntwo", "one\x00two", "x" * 513])
def test_search_query_must_be_a_bounded_single_literal_line(query: str) -> None:
    """Every query is checked, wherever it appears in the list."""
    with pytest.raises(ValidationError):
        SearchRequest(queries=["needle", query])


@pytest.mark.parametrize(
    "start_line,end_line", [(0, None), (-1, None), (2, 1), (1, 202)]
)
def test_read_ranges_follow_the_shared_limits(
    start_line: int, end_line: int | None
) -> None:
    """Read ranges are strict, positive, ordered, and bounded."""
    with pytest.raises(ValidationError):
        ReadRequest(path="Notes/a.md", start_line=start_line, end_line=end_line)


def test_result_models_reuse_the_contract_shapes() -> None:
    """All future tools have object-shaped typed results ready for the adapter."""
    assert SearchResult().model_dump() == {
        "mode": "literal",
        "matches": [],
        "truncated": False,
        "incomplete": False,
        "skipped": {"too_large": 0, "invalid_text": 0, "unreadable": 0},
    }
    assert (
        InfoResult(
            path="Notes/a.md",
            size_bytes=1,
            modified_at="2026-09-22T00:00:00Z",
            line_count=1,
            content_sha256="a" * 64,
            readable=True,
        ).model_dump()["readable"]
        is True
    )


def test_all_result_models_have_json_schema_and_round_trip() -> None:
    """Future adapters can publish each output model as structured JSON."""
    results = [
        SearchResult(
            matches=[
                SearchMatch(path="a.md", line=1, snippet="a", snippet_truncated=False)
            ]
        ),
        ReadResult(
            path="a.md",
            numbered_content="1\ta\n",
            start_line=1,
            end_line=1,
            total_lines=1,
            next_line=None,
            truncated=False,
            content_sha256="a" * 64,
        ),
        ListResult(
            path="",
            entries=[ListEntry(path="a.md", kind="file")],
            next_offset=None,
            truncated=False,
        ),
        InfoResult(
            path="a.md",
            size_bytes=2,
            modified_at="2026-09-22T00:00:00Z",
            line_count=1,
            content_sha256="a" * 64,
            readable=True,
        ),
    ]
    for result in results:
        model_type = type(result)
        assert model_type.model_json_schema()["type"] == "object"
        assert model_type.model_validate_json(result.model_dump_json()) == result
