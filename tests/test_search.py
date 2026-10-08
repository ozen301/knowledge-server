"""Tests for literal search through ripgrep.

These tests run the real ripgrep executable on small synthetic vaults. Tests
of process handling replace it with small shell scripts that misbehave in a
controlled way.
"""

import asyncio
import errno
import os
import shutil
import time
import unicodedata
from pathlib import Path
from typing import Any

import pytest

from knowledge_server.core import search as search_module
from knowledge_server.core.limits import DEFAULT_LIMITS, Limits
from knowledge_server.core.models import (
    DomainErrorCode,
    KnowledgeError,
    SearchRequest,
    SearchResult,
)
from knowledge_server.core.paths import PathPolicy, ResolvedPath
from knowledge_server.core.search import search_notes

_FOUND_RG = shutil.which("rg")
if _FOUND_RG is None:
    raise RuntimeError("ripgrep (rg) must be installed to run the search tests")
RG: str = _FOUND_RG

SECRET = "SENTINEL-SECRET"


def _write(root: Path, relative: str, data: bytes | str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    path.write_bytes(data)
    return path


def _search(
    root: Path,
    query: str | list[str],
    path: str = "",
    *,
    max_results: int = DEFAULT_LIMITS.default_search_results,
    case_sensitive: bool = False,
    limits: Limits = DEFAULT_LIMITS,
    ripgrep: str = RG,
) -> SearchResult:
    request = SearchRequest(
        queries=[query] if isinstance(query, str) else query,
        path=path,
        max_results=max_results,
        case_sensitive=case_sensitive,
    )
    return asyncio.run(search_notes(PathPolicy(root), request, limits, ripgrep=ripgrep))


def _search_error(
    root: Path, query: str | list[str], path: str = "", **kwargs: Any
) -> KnowledgeError:
    with pytest.raises(KnowledgeError) as error:
        _search(root, query, path, **kwargs)
    return error.value


def _hits(result: SearchResult) -> list[tuple[str, int]]:
    return [(match.path, match.line) for match in result.matches]


def _fake_rg(directory: Path, body: str) -> str:
    """Write an executable shell script that stands in for ripgrep."""
    script = directory / "fake-rg"
    script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


class _ProcessRecorder:
    """Record each subprocess that search starts, through the real asyncio call."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.processes: list[asyncio.subprocess.Process] = []
        real_exec = asyncio.create_subprocess_exec

        async def recording_exec(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((args, kwargs))
            process = await real_exec(*args, **kwargs)
            self.processes.append(process)
            return process

        def forbidden_shell(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("search must not start a shell")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", recording_exec)
        monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden_shell)

    def assert_all_reaped(self) -> None:
        assert self.processes
        for process in self.processes:
            assert process.returncode is not None
            with pytest.raises(ProcessLookupError):
                os.kill(process.pid, 0)


# Matching


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    """A vault of ordinary English and Japanese notes."""
    root = tmp_path / "vault"
    _write(
        root,
        "Hardware/NAS.md",
        "# NAS\nUse ECC memory for the NAS.\nECC and Ryzen\nECC Ryzen boards\n",
    )
    _write(root, "日本語/メモ.md", "ネットワーク設定を確認する。\n記憶装置の容量\n")
    _write(root, "My Notes/file name.md", "Run C++ (v2.0)? now.\nuse -rf carefully\n")
    _write(root, "-dash.md", "a --force flag\nregex .* literal\n")
    _write(root, "repeat.md", "cat dog cat dog cat\n")
    return root


@pytest.mark.parametrize(
    ("query", "hits"),
    [
        ("ECC memory", [("Hardware/NAS.md", 2)]),
        ("ECC Ryzen", [("Hardware/NAS.md", 4)]),
        ("ネットワーク", [("日本語/メモ.md", 1)]),
        ("記憶装置", [("日本語/メモ.md", 2)]),
        ("C++ (v2.0)?", [("My Notes/file name.md", 1)]),
        ("-rf", [("My Notes/file name.md", 2)]),
        ("--force", [("-dash.md", 1)]),
        (".*", [("-dash.md", 2)]),
        ("cat", [("repeat.md", 1)]),
    ],
)
def test_literal_matches(notes: Path, query: str, hits: list[tuple[str, int]]) -> None:
    """Phrases, punctuation, CJK, leading dashes, and spaces match literally."""
    result = _search(notes, query)
    assert _hits(result) == hits
    assert result.mode == "literal"
    assert not result.truncated
    assert not result.incomplete


def test_query_is_not_split_into_words(notes: Path) -> None:
    """A phrase matches only where its words appear together."""
    assert _hits(_search(notes, "Ryzen ECC")) == []


def test_queries_are_alternatives(notes: Path) -> None:
    """A line that contains any of the queries matches, in path and line order."""
    result = _search(notes, ["ネットワーク", "absent phrase", "ECC memory"])
    assert _hits(result) == [("Hardware/NAS.md", 2), ("日本語/メモ.md", 1)]
    assert not result.truncated


def test_line_matching_several_queries_gives_one_hit(tmp_path: Path) -> None:
    """Each line appears once, windowed on the first match of any query."""
    _write(tmp_path, "note.md", "xxxx dog yyyy cat zzzz\nabab\ncat\n")
    limits = Limits(max_snippet_length=9)
    result = _search(tmp_path, ["cat", "dog", "aba", "bab", "cat"], limits=limits)
    assert [(m.line, m.snippet) for m in result.matches] == [
        (1, "xx dog yy"),
        (2, "abab"),
        (3, "cat"),
    ]


def test_queries_share_the_result_limit(notes: Path) -> None:
    """A query with many hits in early paths can leave no room for the others."""
    result = _search(notes, ["ネットワーク", "ECC"], max_results=2)
    assert _hits(result) == [("Hardware/NAS.md", 2), ("Hardware/NAS.md", 3)]
    assert result.truncated


def test_repeated_matches_on_one_line_give_one_hit(tmp_path: Path) -> None:
    """A line with several occurrences is one hit, windowed on the first."""
    _write(tmp_path, "note.md", "xxxx cat yyyy cat zzzz\n")
    limits = Limits(max_snippet_length=9)
    result = _search(tmp_path, "cat", limits=limits)
    assert [(m.line, m.snippet) for m in result.matches] == [(1, "xx cat yy")]


def test_no_match(notes: Path) -> None:
    """A query without matches returns an empty, complete result."""
    result = _search(notes, "absent phrase")
    assert result.matches == []
    assert not result.truncated
    assert not result.incomplete
    assert result.skipped.model_dump() == {
        "too_large": 0,
        "invalid_text": 0,
        "unreadable": 0,
    }


def test_empty_vault_and_empty_notes(tmp_path: Path) -> None:
    """Empty directories and empty notes produce an empty result."""
    assert _search(tmp_path, "x").matches == []
    _write(tmp_path, "empty.md", b"")
    _write(tmp_path, "bom.md", b"\xef\xbb\xbf")
    _write(tmp_path, "newline.md", b"\n")
    assert _search(tmp_path, "x").matches == []


@pytest.mark.parametrize(
    ("query", "content", "case_sensitive", "matches"),
    [
        ("ecc", "ECC memory", False, True),
        ("ecc", "ECC memory", True, False),
        ("ECC", "ECC memory", True, True),
        ("émile", "ÉMILE", False, True),
        ("émile", "ÉMILE", True, False),
        ("σοφία", "ΣΟΦΊΑ", False, True),
        ("привет", "ПРИВЕТ", False, True),
        ("ａｂｃ", "ＡＢＣ", False, True),
        # Matched text can have a different UTF-8 length than the query.
        ("k", "\u212a", False, True),
        ("ß", "ẞ", False, True),
    ],
)
def test_case_handling(
    tmp_path: Path, query: str, content: str, case_sensitive: bool, matches: bool
) -> None:
    """Matching ignores case, including non-ASCII case, unless requested."""
    _write(tmp_path, "note.md", f"{content}\n")
    result = _search(tmp_path, query, case_sensitive=case_sensitive)
    assert bool(result.matches) is matches


def test_sort_order(tmp_path: Path) -> None:
    """Hits sort by path in code-point order, then by line number."""
    for relative in ["b.md", "B.md", "a/x.md", "a b/x.md", "é.md", "z.md"]:
        _write(tmp_path, relative, "hit\nmiss\nhit\n")
    result = _search(tmp_path, "hit", max_results=50)
    expected_paths = sorted(["b.md", "B.md", "a/x.md", "a b/x.md", "é.md", "z.md"])
    assert expected_paths[:2] == ["B.md", "a b/x.md"]
    assert _hits(result) == [(p, line) for p in expected_paths for line in (1, 3)]


def test_path_limits_search_to_a_directory_or_file(notes: Path) -> None:
    """A directory path searches recursively; a file path searches one note."""
    _write(notes, "Hardware/Deep/more.md", "ECC again\n")
    assert _hits(_search(notes, "ECC", "Hardware")) == [
        ("Hardware/Deep/more.md", 1),
        ("Hardware/NAS.md", 2),
        ("Hardware/NAS.md", 3),
        ("Hardware/NAS.md", 4),
    ]
    assert _hits(_search(notes, "ECC", "Hardware/")) == _hits(
        _search(notes, "ECC", "Hardware")
    )
    assert _hits(_search(notes, "ECC", "Hardware/Deep/more.md")) == [
        ("Hardware/Deep/more.md", 1)
    ]


# Result limit


@pytest.mark.parametrize(
    ("hit_count", "max_results", "returned", "truncated"),
    [
        (3, 3, 3, False),
        (4, 3, 3, True),
        (1, 1, 1, False),
        (2, 1, 1, True),
        (50, 50, 50, False),
        (51, 50, 50, True),
    ],
)
def test_result_limit_boundaries(
    tmp_path: Path, hit_count: int, max_results: int, returned: int, truncated: bool
) -> None:
    """Exactly `max_results` hits is complete; one more is truncated."""
    _write(tmp_path, "a.md", "hit\n" * (hit_count // 2))
    _write(tmp_path, "b.md", "hit\n" * (hit_count - hit_count // 2))
    result = _search(tmp_path, "hit", max_results=max_results)
    assert len(result.matches) == returned
    assert result.truncated is truncated
    assert not result.incomplete


def test_early_stop_while_input_is_still_being_sent(tmp_path: Path) -> None:
    """Stopping at `max_results + 1` hits is a successful truncated result.

    The input is larger than a pipe buffer, so ripgrep stops reading it before
    search has written all of it.
    """
    line = "hit " + "x" * 1000 + "\n"
    for index in range(8):
        _write(tmp_path, f"n{index}.md", line * 1000)
    result = _search(tmp_path, "hit", max_results=2)
    assert _hits(result) == [("n0.md", 1), ("n0.md", 2)]
    assert result.truncated


def test_injected_limits_reject_larger_requests(tmp_path: Path) -> None:
    """Requests beyond injected result, query-count, or query limits are invalid."""
    _write(tmp_path, "note.md", "abc\n")
    limits = Limits(max_search_results=2, max_search_queries=2, max_query_length=3)
    error = _search_error(tmp_path, "abc", max_results=3, limits=limits)
    assert error.code is DomainErrorCode.INVALID_ARGUMENT
    error = _search_error(tmp_path, ["abc", "abcd"], limits=limits)
    assert error.code is DomainErrorCode.INVALID_ARGUMENT
    error = _search_error(tmp_path, ["a", "b", "c"], limits=limits)
    assert error.code is DomainErrorCode.INVALID_ARGUMENT
    assert _hits(_search(tmp_path, ["abc", "x"], max_results=2, limits=limits)) == [
        ("note.md", 1)
    ]


# Line mapping


def test_line_mapping_with_empty_notes(tmp_path: Path) -> None:
    """Empty notes before, between, and after matches do not shift lines."""
    _write(tmp_path, "a.md", b"")
    _write(tmp_path, "b.md", "miss\nhit b\n")
    _write(tmp_path, "c.md", b"\xef\xbb\xbf")
    _write(tmp_path, "d.md", b"\n")
    _write(tmp_path, "e.md", "hit e")
    _write(tmp_path, "f.md", "hit f\r\nmiss\r\nhit f\r\n")
    _write(tmp_path, "g.md", b"")
    result = _search(tmp_path, "hit")
    assert [(m.path, m.line, m.snippet) for m in result.matches] == [
        ("b.md", 2, "hit b"),
        ("e.md", 1, "hit e"),
        ("f.md", 1, "hit f"),
        ("f.md", 3, "hit f"),
    ]


def test_notes_without_final_newline_stay_separate(tmp_path: Path) -> None:
    """A note's last line never joins the next note's first line."""
    _write(tmp_path, "a.md", "one ha")
    _write(tmp_path, "b.md", "lf two")
    assert _hits(_search(tmp_path, "half")) == []
    assert _hits(_search(tmp_path, "ha")) == [("a.md", 1)]


# Snippet window


def _snippet(
    tmp_path: Path, line: str | bytes, query: str, length: int = 10
) -> tuple[str, bool]:
    _write(tmp_path, "note.md", line)
    limits = Limits(max_snippet_length=length, max_query_length=64)
    result = _search(tmp_path, query, limits=limits)
    assert len(result.matches) == 1
    match = result.matches[0]
    return match.snippet, match.snippet_truncated


@pytest.mark.parametrize(
    ("line", "query", "snippet", "truncated"),
    [
        # The contract's examples.
        ("abcdefghijklmnop", "h", "defghijklm", True),
        ("abcdefghijklmnop", "b", "abcdefghij", True),
        ("abcdefghijklmnop", "o", "ghijklmnop", True),
        ("abcdefghijklmnop", "p", "ghijklmnop", True),
        # An odd extra code point goes after the match.
        ("abcdefghijklmnop", "efg", "bcdefghijk", True),
        ("abcdefghijklmnop", "efgh", "bcdefghijk", True),
        # A match of exactly the limit, and one longer than it.
        ("abcdefghijklmnop", "cdefghijkl", "cdefghijkl", True),
        ("abcdefghijklmnop", "cdefghijklmn", "cdefghijkl", True),
        # Lines that fit are returned whole.
        ("abcdefghij", "j", "abcdefghij", False),
        ("a b", "b", "a b", False),
        ("  padded  ", "padded", "  padded  ", False),
        # Code points, not bytes.
        ("一二三四五六七八九十百千万", "八", "四五六七八九十百千万", True),
        ("一二三四五六七八九十百千万", "三", "一二三四五六七八九十", True),
        ("ÉÉÉÉÉÉÉÉÉÉxÉÉÉÉÉÉÉÉÉÉ", "x", "ÉÉÉÉxÉÉÉÉÉ", True),
        # Characters that are not line boundaries stay in the line.
        ("a\rb", "b", "a\rb", False),
        ("a﻿b", "b", "a﻿b", False),
        ("ab\u0085c", "c", "ab\u0085c", False),
    ],
)
def test_snippet_window(
    tmp_path: Path, line: str, query: str, snippet: str, truncated: bool
) -> None:
    """Snippets follow the contract's window rule."""
    assert _snippet(tmp_path, f"{line}\n", query) == (snippet, truncated)


def test_snippet_window_uses_matched_text_not_query(tmp_path: Path) -> None:
    """Window positions come from the reported match, in code points."""
    # "k" (1 byte) matches the Kelvin sign (3 bytes); "ß" (2 bytes) matches
    # "ẞ" (3 bytes).
    line = "abcdefgh\u212aijlmnopq\n"
    assert _snippet(tmp_path, line, "k") == ("efgh\u212aijlmn", True)
    assert _snippet(tmp_path, "ẞẞẞẞẞ abcdefgh\n", "ß") == ("ẞẞẞẞẞ abcd", True)


def test_snippet_excludes_bom_and_line_ending(tmp_path: Path) -> None:
    """The snippet is the reader's version of the line."""
    assert _snippet(tmp_path, b"\xef\xbb\xbfab h\r\nnext\r\n", "h") == ("ab h", False)
    assert _snippet(tmp_path, b"\xef\xbb\xbf\xef\xbb\xbfh\n", "h") == (
        "﻿h",
        False,
    )


def test_snippet_with_default_limit(tmp_path: Path) -> None:
    """The default window is 300 code points."""
    line = "a" * 400 + "needle" + "b" * 400
    snippet, truncated = _snippet(tmp_path, f"{line}\n", "needle", length=300)
    assert len(snippet) == 300
    assert snippet == "a" * 147 + "needle" + "b" * 147
    assert truncated


# Unicode representations (known limitation pending NFC support)


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("café", "café"),
        ("かがみ", "かがみ"),
    ],
)
def test_identical_representations_match(tmp_path: Path, text: str, query: str) -> None:
    """Text and query in the same Unicode form match, composed or decomposed."""
    for form in ("NFC", "NFD"):
        _write(tmp_path, "note.md", unicodedata.normalize(form, text) + "\n")
        result = _search(tmp_path, unicodedata.normalize(form, query))
        assert _hits(result) == [("note.md", 1)]


@pytest.mark.parametrize(
    ("composed", "decomposed"),
    [
        ("café", "cafe\u0301"),
        ("がみ", "か\u3099み"),
    ],
)
def test_different_representations_miss(
    tmp_path: Path, composed: str, decomposed: str
) -> None:
    """Limitation pending NFC support: equivalent forms do not match.

    `é` and `e` plus a combining accent, or `が` and `か` plus a combining voiced
    mark, look the same but are different code points.
    """
    assert unicodedata.normalize("NFC", decomposed) == composed
    _write(tmp_path, "note.md", composed + "\n")
    assert _search(tmp_path, decomposed).matches == []
    _write(tmp_path, "note.md", decomposed + "\n")
    assert _search(tmp_path, composed).matches == []


# Skipped notes and explicit targets


def test_recursive_search_counts_skipped_notes(tmp_path: Path) -> None:
    """Oversized and invalid notes are skipped, counted, and mark incomplete."""
    limits = Limits(max_file_bytes=64)
    _write(tmp_path, "good.md", "hit\n")
    _write(tmp_path, "big.md", "hit\n" * 20)
    _write(tmp_path, "latin1.md", b"hit caf\xe9\n")
    _write(tmp_path, "nul.md", b"hit\x00\n")
    result = _search(tmp_path, "hit", limits=limits)
    assert _hits(result) == [("good.md", 1)]
    assert result.incomplete
    assert not result.truncated
    assert result.skipped.model_dump() == {
        "too_large": 1,
        "invalid_text": 2,
        "unreadable": 0,
    }


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"hit\n" * 20, DomainErrorCode.FILE_TOO_LARGE),
        (b"hit caf\xe9\n", DomainErrorCode.INVALID_ENCODING),
        (b"hit\x00\n", DomainErrorCode.INVALID_ENCODING),
    ],
)
def test_explicit_unreadable_target_is_an_error(
    tmp_path: Path, data: bytes, code: DomainErrorCode
) -> None:
    """An explicit file target that cannot be searched returns an error."""
    _write(tmp_path, "note.md", data)
    error = _search_error(tmp_path, "hit", "note.md", limits=Limits(max_file_bytes=64))
    assert error.code is code


def _remove_before_loading(
    monkeypatch: pytest.MonkeyPatch, root: Path, relative_path: str
) -> None:
    real_load = search_module.load_note_text

    def removing_load(
        policy: PathPolicy, resolved: ResolvedPath, *args: Any, **kwargs: Any
    ) -> Any:
        if resolved.relative_path == relative_path:
            (root / relative_path).unlink()
        return real_load(policy, resolved, *args, **kwargs)

    monkeypatch.setattr(search_module, "load_note_text", removing_load)


def test_note_removed_before_loading_is_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """During recursive search, a vanished note is counted as unreadable."""
    _write(tmp_path, "gone.md", "hit\n")
    _write(tmp_path, "kept.md", "hit\n")
    _remove_before_loading(monkeypatch, tmp_path, "gone.md")
    result = _search(tmp_path, "hit")
    assert _hits(result) == [("kept.md", 1)]
    assert result.incomplete
    assert result.skipped.unreadable == 1


def test_explicit_target_removed_before_loading_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit target that vanishes follows the error mappings."""
    _write(tmp_path, "gone.md", "hit\n")
    _remove_before_loading(monkeypatch, tmp_path, "gone.md")
    assert _search_error(tmp_path, "hit", "gone.md").code is DomainErrorCode.NOT_FOUND


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permissions")
def test_unreadable_note_and_directory_are_counted(tmp_path: Path) -> None:
    """A note or subdirectory without read permission counts as unreadable."""
    _write(tmp_path, "kept.md", "hit\n")
    locked_note = _write(tmp_path, "locked.md", "hit\n")
    _write(tmp_path, "locked/inner.md", "hit\n")
    locked_note.chmod(0)
    (tmp_path / "locked").chmod(0)
    try:
        result = _search(tmp_path, "hit")
    finally:
        (tmp_path / "locked").chmod(0o755)
        locked_note.chmod(0o644)
    assert _hits(result) == [("kept.md", 1)]
    assert result.incomplete
    assert result.skipped.unreadable == 2


# Privacy


@pytest.fixture
def private_vault(tmp_path: Path) -> Path:
    """A vault whose only visible secret-free note matches, plus hidden secrets."""
    root = tmp_path / "vault"
    root.mkdir()
    outside = _write(tmp_path, "outside.md", f"{SECRET} outside\n")
    _write(tmp_path, "outside-dir/note.md", f"{SECRET} outside dir\n")
    _write(root, "visible.md", f"{SECRET[:8]} visible\n")
    _write(root, ".hidden.md", f"{SECRET} hidden\n")
    _write(root, ".obsidian/config.md", f"{SECRET} config\n")
    _write(root, "sub/.secret.md", f"{SECRET} nested hidden\n")
    _write(root, "notes.txt", f"{SECRET} text file\n")
    (root / "file-link.md").symlink_to(outside)
    (root / "dir-link").symlink_to(tmp_path / "outside-dir")
    return root


def test_hidden_outside_and_symlinked_secrets_never_appear(private_vault: Path) -> None:
    """Only visible notes are searched; excluded names are not revealed."""
    result = _search(private_vault, SECRET[:8])
    assert _hits(result) == [("visible.md", 1)]
    assert not result.incomplete
    dumped = result.model_dump_json()
    assert SECRET not in dumped
    for name in ["hidden", "obsidian", "secret", "notes.txt", "link", "outside"]:
        assert name not in dumped
    assert _search(private_vault, SECRET).matches == []


@pytest.mark.parametrize(
    ("path", "code"),
    [
        (".hidden.md", DomainErrorCode.ACCESS_DENIED),
        (".obsidian", DomainErrorCode.ACCESS_DENIED),
        ("sub/.secret.md", DomainErrorCode.ACCESS_DENIED),
        ("file-link.md", DomainErrorCode.ACCESS_DENIED),
        ("dir-link", DomainErrorCode.ACCESS_DENIED),
        ("dir-link/note.md", DomainErrorCode.ACCESS_DENIED),
        ("notes.txt", DomainErrorCode.UNSUPPORTED_TYPE),
        ("missing.md", DomainErrorCode.NOT_FOUND),
        ("missing", DomainErrorCode.NOT_FOUND),
        ("../outside.md", DomainErrorCode.INVALID_PATH),
        ("/etc", DomainErrorCode.INVALID_PATH),
        ("visible.md/", DomainErrorCode.NOT_A_DIRECTORY),
    ],
)
def test_explicit_disallowed_targets(
    private_vault: Path, path: str, code: DomainErrorCode
) -> None:
    """Explicit targets follow the shared path policy."""
    error = _search_error(private_vault, SECRET, path)
    assert error.code is code
    assert SECRET not in str(error)
    assert str(private_vault) not in str(error)


def test_note_swapped_for_symlink_before_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink swapped in after discovery is not followed."""
    root = tmp_path / "vault"
    _write(root, "note.md", "harmless\n")
    outside = _write(tmp_path, "outside.md", f"{SECRET}\n")
    real_load = search_module.load_note_text

    def swapping_load(
        policy: PathPolicy, resolved: ResolvedPath, *args: Any, **kwargs: Any
    ) -> Any:
        (root / "note.md").unlink()
        (root / "note.md").symlink_to(outside)
        return real_load(policy, resolved, *args, **kwargs)

    monkeypatch.setattr(search_module, "load_note_text", swapping_load)
    result = _search(root, SECRET)
    assert result.matches == []
    assert result.skipped.unreadable == 1
    assert SECRET not in result.model_dump_json()


def test_ignore_files_do_not_hide_notes(tmp_path: Path) -> None:
    """Git and ripgrep ignore files do not change what search sees."""
    _write(tmp_path, ".gitignore", "ignored.md\nignored-dir/\n")
    _write(tmp_path, ".ignore", "*.md\n")
    _write(tmp_path, ".rgignore", "*\n")
    _write(tmp_path, "ignored.md", "hit\n")
    _write(tmp_path, "ignored-dir/note.md", "hit\n")
    (tmp_path / ".git").mkdir()
    assert _hits(_search(tmp_path, "hit")) == [
        ("ignored-dir/note.md", 1),
        ("ignored.md", 1),
    ]


def test_search_does_not_change_the_vault(notes: Path) -> None:
    """Search only reads the vault."""

    def snapshot() -> dict[str, tuple[float, bytes]]:
        return {
            str(path.relative_to(notes)): (path.stat().st_mtime, path.read_bytes())
            for path in notes.rglob("*")
            if path.is_file()
        }

    before = snapshot()
    _search(notes, "ECC")
    assert snapshot() == before


# Budgets


def test_entry_budget(tmp_path: Path) -> None:
    """Every inspected entry counts, but excluded directories are not entered."""
    _write(tmp_path, "a.md", "hit\n")
    _write(tmp_path, "b.txt", "hit\n")
    for index in range(5):
        _write(tmp_path, f".hidden/{index}.md", "hit\n")
    _write(tmp_path, "sub/c.md", "hit\n")
    # Inspected: a.md, b.txt, .hidden, sub, and sub/c.md.
    result = _search(tmp_path, "hit", limits=Limits(max_search_entries=5))
    assert _hits(result) == [("a.md", 1), ("sub/c.md", 1)]
    error = _search_error(tmp_path, "hit", limits=Limits(max_search_entries=4))
    assert error.code is DomainErrorCode.SEARCH_LIMIT_EXCEEDED
    result = _search(tmp_path, "hit", "sub", limits=Limits(max_search_entries=1))
    assert _hits(result) == [("sub/c.md", 1)]


def test_source_byte_budget_counts_bytes_read(tmp_path: Path) -> None:
    """Loaded bytes count, including a sentinel byte and skipped notes."""
    _write(tmp_path, "a.md", "hit a\n" + "x" * 4)  # 10 bytes
    _write(tmp_path, "b.md", "x" * 100)  # oversized: 65 bytes read
    _write(tmp_path, "c.md", "hit c")  # 5 bytes
    exact = Limits(max_file_bytes=64, max_search_source_bytes=80)
    result = _search(tmp_path, "hit", limits=exact)
    assert _hits(result) == [("a.md", 1), ("c.md", 1)]
    assert result.skipped.too_large == 1
    over = Limits(max_file_bytes=64, max_search_source_bytes=79)
    error = _search_error(tmp_path, "hit", limits=over)
    assert error.code is DomainErrorCode.SEARCH_LIMIT_EXCEEDED


def test_source_byte_budget_bounds_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loading stops once the budget is exceeded instead of reading on."""
    for index in range(4):
        _write(tmp_path, f"n{index}.md", "x" * 50)
    requested: list[int] = []
    real_read = os.read

    def tracking_read(fd: int, size: int) -> bytes:
        requested.append(size)
        return real_read(fd, size)

    monkeypatch.setattr(os, "read", tracking_read)
    limits = Limits(max_file_bytes=64, max_search_source_bytes=120)
    error = _search_error(tmp_path, "x", limits=limits)
    assert error.code is DomainErrorCode.SEARCH_LIMIT_EXCEEDED
    assert sum(requested) <= 120 + 1 + 64


def test_source_byte_budget_counts_bytes_before_a_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bytes read from a note that then fails to read still count."""
    for index in range(4):
        _write(tmp_path, f"n{index}.md", "x" * 50)
    real_read = os.read
    reads: dict[int, int] = {}

    def failing_second_read(fd: int, size: int) -> bytes:
        reads[fd] = reads.get(fd, 0) + 1
        if reads[fd] > 1:
            raise OSError(errno.EIO, "read failed")
        return real_read(fd, size)

    def fresh_counts(*args: Any, **kwargs: Any) -> Any:
        reads.clear()
        return real_load(*args, **kwargs)

    real_load = search_module.load_note_text
    monkeypatch.setattr(search_module, "load_note_text", fresh_counts)
    monkeypatch.setattr(os, "read", failing_second_read)
    limits = Limits(max_file_bytes=64, max_search_source_bytes=120)
    error = _search_error(tmp_path, "x", limits=limits)
    assert error.code is DomainErrorCode.SEARCH_LIMIT_EXCEEDED


def test_output_budget_with_real_ripgrep(tmp_path: Path) -> None:
    """Long matching lines can exceed the output budget."""
    _write(tmp_path, "note.md", ("hit " + "x" * 2000 + "\n") * 20)
    limits = Limits(max_search_output_bytes=10_000)
    error = _search_error(tmp_path, "hit", max_results=50, limits=limits)
    assert error.code is DomainErrorCode.SEARCH_LIMIT_EXCEEDED


def test_deadline_covers_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deadline stops a search that is still loading notes."""
    for index in range(20):
        _write(tmp_path, f"n{index}.md", "hit\n")
    real_load = search_module.load_note_text

    def slow_load(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.05)
        return real_load(*args, **kwargs)

    monkeypatch.setattr(search_module, "load_note_text", slow_load)
    started = time.monotonic()
    error = _search_error(tmp_path, "hit", limits=Limits(search_deadline_seconds=0.2))
    assert error.code is DomainErrorCode.SEARCH_LIMIT_EXCEEDED
    assert time.monotonic() - started < 0.6


# Process handling


def test_process_arguments_and_environment(
    notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each query reaches ripgrep as its own `-e` value, without a shell."""
    recorder = _ProcessRecorder(monkeypatch)
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", "/nonexistent/config")
    query = "-e --files $(touch x) ; *"
    _search(notes, [query, "--json"])
    _search(notes, "ECC", case_sensitive=True)
    (insensitive_args, kwargs), (sensitive_args, _) = recorder.calls
    for args in (insensitive_args, sensitive_args):
        assert args[0] == RG
        assert all(isinstance(arg, str) for arg in args)
        for flag in ["--no-config", "--json", "--fixed-strings"]:
            assert flag in args
        assert args[args.index("--encoding") + 1] == "none"
        assert args[-1] == "-"
    assert "--ignore-case" in insensitive_args
    assert "--case-sensitive" not in insensitive_args
    assert "--case-sensitive" in sensitive_args
    assert "--ignore-case" not in sensitive_args
    first = insensitive_args.index("-e")
    assert insensitive_args[first : first + 4] == ("-e", query, "-e", "--json")
    assert insensitive_args.count(query) == 1
    assert insensitive_args[insensitive_args.index("--max-count") + 1] == "21"
    assert str(notes) not in " ".join(insensitive_args)
    env = kwargs["env"]
    assert isinstance(env, dict)
    assert not any(name.startswith("RIPGREP") for name in env)
    assert kwargs.get("cwd") is not None
    assert not (Path(kwargs["cwd"]) / "x").exists()
    recorder.assert_all_reaped()


def test_user_config_does_not_change_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ripgrep configuration file in the environment has no effect."""
    config = _write(
        tmp_path, "rgrc", "--word-regexp\n--case-sensitive\n--max-count=1\n"
    )
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", str(config))
    root = tmp_path / "vault"
    _write(root, "note.md", "ECCmemory\necc\n")
    assert _hits(_search(root, "ecc")) == [("note.md", 1), ("note.md", 2)]


def test_no_ripgrep_run_without_eligible_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With nothing to search, the result is empty without a process."""
    recorder = _ProcessRecorder(monkeypatch)
    _write(tmp_path, "empty.md", b"")
    assert _search(tmp_path, "hit").matches == []
    assert recorder.calls == []


@pytest.mark.parametrize(
    "body",
    [
        "cat > /dev/null; echo 'rg: error' >&2; exit 2",
        "cat > /dev/null; exit 3",
        "kill -9 $$",
        "cat > /dev/null; exit 0",
        "cat > /dev/null; echo 'not json'; exit 0",
        'cat > /dev/null; printf \'{"type":"match"}\'; exit 0',
        # Binary handling would have changed the line numbers.
        (
            "cat > /dev/null; printf '%s\\n' "
            '\'{"type":"end","data":{"binary_offset":7}}\'; exit 1'
        ),
        # A match reported together with the no-match status.
        (
            "cat > /dev/null; printf '%s\\n' "
            '\'{"type":"match","data":{"lines":{"text":"hit\\n"},'
            '"line_number":1,"absolute_offset":0,'
            '"submatches":[{"match":{"text":"hit"},"start":0,"end":3}]}}\'; exit 1'
        ),
    ],
)
def test_process_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """Errors, signals, and malformed output are `SEARCH_FAILED`."""
    recorder = _ProcessRecorder(monkeypatch)
    root = tmp_path / "vault"
    _write(root, "note.md", "hit\n")
    error = _search_error(root, "hit", ripgrep=_fake_rg(tmp_path, body))
    assert error.code is DomainErrorCode.SEARCH_FAILED
    assert "rg: error" not in str(error)
    recorder.assert_all_reaped()


@pytest.mark.parametrize(
    ("text", "line_number", "offset", "start", "end"),
    [
        # Text that was not sent.
        (f"{SECRET}\\n", 1, 0, 0, 3),
        # A line number or offset that does not match the sent text.
        ("hit\\n", 2, 0, 0, 3),
        ("hit\\n", 1, 4, 0, 3),
        ("hit\\n", 5, 12, 0, 3),
        # A match outside the line or not on a character boundary.
        ("hit\\n", 1, 0, 2, 9),
        ("é hit\\n", 3, 9, 1, 2),
    ],
)
def test_inconsistent_ripgrep_output_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    line_number: int,
    offset: int,
    start: int,
    end: int,
) -> None:
    """A reported match that does not agree with the sent text fails."""
    root = tmp_path / "vault"
    _write(root, "note.md", "hit\nmiss\né hit\n")
    record = (
        '{"type":"match","data":{"path":{"text":"<stdin>"},'
        f'"lines":{{"text":"{text}"}},"line_number":{line_number},'
        f'"absolute_offset":{offset},'
        f'"submatches":[{{"match":{{"text":"x"}},"start":{start},"end":{end}}}]}}}}'
    )
    ripgrep = _fake_rg(tmp_path, f"cat > /dev/null; printf '%s\\n' '{record}'; exit 0")
    error = _search_error(root, "hit", ripgrep=ripgrep)
    assert error.code is DomainErrorCode.SEARCH_FAILED
    assert SECRET not in str(error)


def test_missing_executable(tmp_path: Path) -> None:
    """A ripgrep executable that cannot start is `SEARCH_FAILED`."""
    _write(tmp_path, "note.md", "hit\n")
    error = _search_error(tmp_path, "hit", ripgrep=str(tmp_path / "missing-rg"))
    assert error.code is DomainErrorCode.SEARCH_FAILED
    assert str(tmp_path) not in str(error)


def test_timeout_kills_and_reaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A search that runs past the deadline stops and leaves no process."""
    recorder = _ProcessRecorder(monkeypatch)
    root = tmp_path / "vault"
    _write(root, "note.md", "hit\n")
    ripgrep = _fake_rg(tmp_path, "exec sleep 30")
    started = time.monotonic()
    error = _search_error(
        root, "hit", ripgrep=ripgrep, limits=Limits(search_deadline_seconds=0.3)
    )
    assert error.code is DomainErrorCode.SEARCH_LIMIT_EXCEEDED
    assert time.monotonic() - started < 3
    recorder.assert_all_reaped()


def test_cancellation_kills_and_reaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling a running search stops ripgrep and propagates."""
    recorder = _ProcessRecorder(monkeypatch)
    root = tmp_path / "vault"
    _write(root, "note.md", "hit\n")
    ripgrep = _fake_rg(tmp_path, "exec sleep 30")

    async def run_and_cancel() -> None:
        task = asyncio.create_task(
            search_notes(
                PathPolicy(root), SearchRequest(queries=["hit"]), ripgrep=ripgrep
            )
        )
        while not recorder.processes:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    started = time.monotonic()
    asyncio.run(run_and_cancel())
    assert time.monotonic() - started < 3
    recorder.assert_all_reaped()


def test_second_cancellation_during_cleanup_still_reaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancellation that arrives while ripgrep is being reaped is deferred."""
    recorder = _ProcessRecorder(monkeypatch)
    root = tmp_path / "vault"
    _write(root, "note.md", "hit\n")
    ripgrep = _fake_rg(tmp_path, "exec sleep 30")

    async def run_and_cancel_twice() -> None:
        task = asyncio.create_task(
            search_notes(
                PathPolicy(root), SearchRequest(queries=["hit"]), ripgrep=ripgrep
            )
        )
        while not recorder.processes:
            await asyncio.sleep(0.01)
        process = recorder.processes[0]
        real_wait = process.wait
        reaping = asyncio.Event()

        async def slow_wait() -> int:
            reaping.set()
            await asyncio.sleep(0.2)
            return await real_wait()

        monkeypatch.setattr(process, "wait", slow_wait)
        task.cancel()
        await reaping.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None

    asyncio.run(run_and_cancel_twice())
    recorder.assert_all_reaped()


def test_cancellation_during_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling while notes are loading propagates and stops the loading."""
    for index in range(50):
        _write(tmp_path, f"n{index}.md", "hit\n")
    loaded: list[str] = []
    real_load = search_module.load_note_text

    def slow_load(
        policy: PathPolicy, resolved: ResolvedPath, *args: Any, **kwargs: Any
    ) -> Any:
        loaded.append(resolved.relative_path)
        time.sleep(0.02)
        return real_load(policy, resolved, *args, **kwargs)

    monkeypatch.setattr(search_module, "load_note_text", slow_load)

    async def run_and_cancel() -> None:
        task = asyncio.create_task(
            search_notes(
                PathPolicy(tmp_path), SearchRequest(queries=["hit"]), ripgrep=RG
            )
        )
        while not loaded:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_and_cancel())
    time.sleep(0.1)
    count = len(loaded)
    time.sleep(0.1)
    assert len(loaded) == count < 50


@pytest.mark.parametrize(
    "body",
    [
        # Endless well-formed records on stdout.
        'exec yes \'{"type":"begin","data":{"path":{"text":"<stdin>"}}}\'',
        # Endless output on stderr.
        "exec yes 'rg: error' >&2",
        # One endless record without a newline.
        "exec tr '\\000' x < /dev/zero",
    ],
)
def test_output_budget_is_enforced_while_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    """Endless output stops at the budget instead of being captured first."""
    recorder = _ProcessRecorder(monkeypatch)
    root = tmp_path / "vault"
    _write(root, "note.md", "hit\n")
    started = time.monotonic()
    error = _search_error(
        root,
        "hit",
        ripgrep=_fake_rg(tmp_path, body),
        limits=Limits(max_search_output_bytes=256 * 1024),
    )
    assert error.code is DomainErrorCode.SEARCH_LIMIT_EXCEEDED
    assert time.monotonic() - started < 3
    recorder.assert_all_reaped()


def test_search_failure_messages_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Errors contain no query text, host paths, or subprocess output."""
    root = tmp_path / "vault"
    _write(root, "note.md", f"{SECRET}\n")
    ripgrep = _fake_rg(tmp_path, f"echo '{SECRET} {tmp_path}' >&2; exit 2")
    error = _search_error(root, SECRET, ripgrep=ripgrep)
    message = str(error)
    assert SECRET not in message
    assert str(tmp_path) not in message
    assert error.code is DomainErrorCode.SEARCH_FAILED
