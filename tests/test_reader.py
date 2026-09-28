"""Tests for bounded reading, directory listing, and file metadata."""

import errno
import hashlib
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from knowledge_server.core.limits import DEFAULT_LIMITS, Limits
from knowledge_server.core.models import (
    DomainErrorCode,
    InfoRequest,
    KnowledgeError,
    ListRequest,
    ReadRequest,
    ReadResult,
)
from knowledge_server.core.paths import PathPolicy, ResolvedPath, TargetKind
from knowledge_server.core.reader import list_directory, note_info, read_note

SMALL = Limits(
    max_file_bytes=64,
    max_read_content_bytes=8,
    default_read_lines=3,
    max_read_lines=5,
    max_directory_page=3,
)
TEN_LINES = "".join(f"{number}\n" for number in range(1, 11))


def _write(root: Path, relative: str, data: bytes | str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode()
    path.write_bytes(data)
    return path


def _error_code(operation: Callable[..., object], *args: object) -> DomainErrorCode:
    with pytest.raises(KnowledgeError) as error:
        operation(*args)
    return error.value.code


def _both_operations(
    policy: PathPolicy, path: str, limits: Limits = DEFAULT_LIMITS
) -> list[Callable[[], object]]:
    """Return read and info calls for one path, to check shared failures."""
    return [
        lambda: read_note(policy, ReadRequest(path=path), limits=limits),
        lambda: note_info(policy, InfoRequest(path=path), limits=limits),
    ]


def _read(
    root: Path,
    path: str,
    start: int = 1,
    end: int | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> ReadResult:
    request = ReadRequest(path=path, start_line=start, end_line=end)
    return read_note(PathPolicy(root), request, limits=limits)


# Read: line representation


@pytest.mark.parametrize(
    ("data", "content", "total_lines"),
    [
        (b"", "", 0),
        (b"\xef\xbb\xbf", "", 0),
        (b"\n", "\n", 1),
        (b"\xef\xbb\xbf\n", "\n", 1),
        (b"a\nb", "a\nb\n", 2),
        (b"a\nb\n", "a\nb\n", 2),
        (b"a\r\nb\r\n", "a\nb\n", 2),
        (b"\xef\xbb\xbf# Title\r\nBody", "# Title\nBody\n", 2),
        (b"a\rb\n", "a\rb\n", 1),
        (
            "u\u2028v\u2029w\u0085x\x0cy\n".encode(),
            "u\u2028v\u2029w\u0085x\x0cy\n",
            1,
        ),
        ("a\ufeffb\n".encode(), "a\ufeffb\n", 1),
        (b"\n\n\n", "\n\n\n", 3),
        (b"a\r\r\n", "a\r\n", 1),
    ],
)
def test_line_representation(
    tmp_path: Path, data: bytes, content: str, total_lines: int
) -> None:
    """BOM removal, CRLF normalization, and LF-only line splitting."""
    _write(tmp_path, "note.md", data)
    result = _read(tmp_path, "note.md")
    assert result.content == content
    assert result.total_lines == total_lines
    assert result.content_sha256 == hashlib.sha256(data).hexdigest()


def test_unicode_content_and_path(tmp_path: Path) -> None:
    """Unicode filenames and content are preserved."""
    _write(tmp_path, "日本語 ノート/café.md", "こんにちは\n世界\n")
    result = _read(tmp_path, "日本語 ノート/café.md")
    assert result.path == "日本語 ノート/café.md"
    assert result.content == "こんにちは\n世界\n"


# Read: ranges and continuation


@pytest.mark.parametrize(
    ("start", "end", "bounds", "next_line", "content"),
    [
        (1, 5, (1, 5), 6, "1\n2\n3\n4\n5\n"),
        (6, 10, (6, 10), None, "6\n7\n8\n9\n10\n"),
        (6, None, (6, 10), None, "6\n7\n8\n9\n10\n"),
        (8, 20, (8, 10), None, "8\n9\n10\n"),
        (10, 10, (10, 10), None, "10\n"),
        (11, None, (None, None), None, ""),
        (50, 60, (None, None), None, ""),
    ],
)
def test_ranges_and_eof(
    tmp_path: Path,
    start: int,
    end: int | None,
    bounds: tuple[int | None, int | None],
    next_line: int | None,
    content: str,
) -> None:
    """Contract examples for a ten-line file."""
    _write(tmp_path, "ten.md", TEN_LINES)
    result = _read(tmp_path, "ten.md", start, end)
    assert (result.start_line, result.end_line) == bounds
    assert result.next_line == next_line
    assert result.truncated is False
    assert result.total_lines == 10
    assert result.content == content


def test_default_range_uses_limit(tmp_path: Path) -> None:
    """A null end requests the default number of lines."""
    _write(tmp_path, "ten.md", TEN_LINES)
    result = _read(tmp_path, "ten.md", 2, limits=SMALL)
    assert (result.start_line, result.end_line, result.next_line) == (2, 4, 5)
    assert result.truncated is False


def test_default_range_with_default_limits(tmp_path: Path) -> None:
    """The default read range is 200 lines."""
    _write(tmp_path, "many.md", "x\n" * 250)
    result = _read(tmp_path, "many.md")
    assert (result.start_line, result.end_line, result.next_line) == (1, 200, 201)
    assert result.truncated is False


def test_empty_file_start_after_eof(tmp_path: Path) -> None:
    """An empty file has no readable lines."""
    _write(tmp_path, "empty.md", b"")
    result = _read(tmp_path, "empty.md")
    assert (result.start_line, result.end_line, result.next_line) == (None, None, None)
    assert result.total_lines == 0
    assert result.truncated is False


def test_explicit_range_over_injected_maximum_is_invalid(tmp_path: Path) -> None:
    """Operations enforce the injected maximum read range."""
    _write(tmp_path, "ten.md", TEN_LINES)
    code = _error_code(_read, tmp_path, "ten.md", 1, 6, SMALL)
    assert code is DomainErrorCode.INVALID_ARGUMENT


def test_byte_limit_continuation(tmp_path: Path) -> None:
    """Stopping at the byte limit reports truncation and the next line."""
    _write(tmp_path, "note.md", "aaa\nbbb\ncc\nd\n")
    first = _read(tmp_path, "note.md", 1, 4, limits=SMALL)
    assert first.content == "aaa\nbbb\n"
    assert (first.start_line, first.end_line, first.next_line) == (1, 2, 3)
    assert first.truncated is True
    rest = _read(tmp_path, "note.md", 3, 4, limits=SMALL)
    assert rest.content == "cc\nd\n"
    assert rest.next_line is None
    assert rest.truncated is False
    assert first.content + rest.content == "aaa\nbbb\ncc\nd\n"


def test_small_range_stopping_at_limit_is_not_truncated(tmp_path: Path) -> None:
    """A range that fits exactly is complete even if more lines follow."""
    _write(tmp_path, "note.md", "aaa\nbbb\nccc\n")
    result = _read(tmp_path, "note.md", 1, 2, limits=SMALL)
    assert result.content == "aaa\nbbb\n"
    assert result.next_line == 3
    assert result.truncated is False


def test_limit_counts_utf8_bytes_after_normalization(tmp_path: Path) -> None:
    """The limit counts returned UTF-8 bytes, not raw bytes or characters."""
    # Raw line 1 is 9 bytes with its BOM and CRLF, but 8 bytes when returned.
    _write(tmp_path, "crlf.md", b"\xef\xbb\xbfabcdefg\r\nx\n")
    result = _read(tmp_path, "crlf.md", 1, 1, limits=SMALL)
    assert result.content == "abcdefg\n"
    # "ééé" is three characters but six bytes; with LF it is seven bytes.
    _write(tmp_path, "accent.md", "ééé\né\n")
    result = _read(tmp_path, "accent.md", 1, 2, limits=SMALL)
    assert result.content == "ééé\n"
    assert result.truncated is True
    assert result.next_line == 2


@pytest.mark.parametrize(
    ("line", "fits"),
    [
        ("x" * (DEFAULT_LIMITS.max_read_content_bytes - 1), True),
        ("x" * DEFAULT_LIMITS.max_read_content_bytes, False),
    ],
)
def test_exact_cap_with_default_limit(tmp_path: Path, line: str, fits: bool) -> None:
    """A 32,767-byte line plus its LF fits; a 32,768-byte line does not."""
    # The last line has no final newline; its synthesized LF still counts.
    _write(tmp_path, "cap.md", line)
    if fits:
        result = _read(tmp_path, "cap.md")
        assert len(result.content.encode()) == DEFAULT_LIMITS.max_read_content_bytes
        assert result.truncated is False
    else:
        code = _error_code(_read, tmp_path, "cap.md")
        assert code is DomainErrorCode.LINE_TOO_LONG


def test_later_long_line(tmp_path: Path) -> None:
    """Continuation stops before a long line, which then fails on its own."""
    _write(tmp_path, "note.md", "a\n" + "y" * 20 + "\nb\n")
    first = _read(tmp_path, "note.md", 1, 3, limits=SMALL)
    assert first.content == "a\n"
    assert first.next_line == 2
    assert first.truncated is True
    code = _error_code(_read, tmp_path, "note.md", 2, 3, SMALL)
    assert code is DomainErrorCode.LINE_TOO_LONG
    after = _read(tmp_path, "note.md", 3, 3, limits=SMALL)
    assert after.content == "b\n"


# Read: content and size checks


@pytest.mark.parametrize(
    "data",
    [b"\xff\xfe", b"ok\x00\n", b"\xed\xa0\x80\n", b"caf\xc3", b"\xef\xbb"],
)
def test_invalid_encoding(tmp_path: Path, data: bytes) -> None:
    """Invalid UTF-8, encoded surrogates, and NUL bytes are rejected."""
    _write(tmp_path, "bad.md", data)
    assert _error_code(_read, tmp_path, "bad.md") is DomainErrorCode.INVALID_ENCODING


def test_size_limit_uses_raw_bytes(tmp_path: Path) -> None:
    """A file at the limit is readable; one byte more is too large."""
    _write(tmp_path, "at.md", b"x" * 63 + b"\n")
    info = note_info(PathPolicy(tmp_path), InfoRequest(path="at.md"), limits=SMALL)
    assert info.readable is True
    # CRLF normalization would bring this file within the limit.
    _write(tmp_path, "over.md", b"x" * 63 + b"\r\n")
    code = _error_code(_read, tmp_path, "over.md", 1, 1, SMALL)
    assert code is DomainErrorCode.FILE_TOO_LARGE
    # The BOM also counts toward the raw size.
    _write(tmp_path, "bom.md", b"\xef\xbb\xbf" + b"x" * 62)
    code = _error_code(_read, tmp_path, "bom.md", 1, 1, SMALL)
    assert code is DomainErrorCode.FILE_TOO_LARGE


def test_oversized_file_is_not_fully_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Loading reads at most the size limit plus one sentinel byte."""
    _write(tmp_path, "big.md", b"x" * 10_000)
    requested: list[int] = []
    real_read = os.read

    def tracking_read(fd: int, size: int) -> bytes:
        requested.append(size)
        return real_read(fd, size)

    monkeypatch.setattr(os, "read", tracking_read)
    code = _error_code(_read, tmp_path, "big.md", 1, 1, SMALL)
    assert code is DomainErrorCode.FILE_TOO_LARGE
    assert sum(requested) <= SMALL.max_file_bytes + 1


# Read and info: path policy and I/O failures


@pytest.fixture
def policy_vault(tmp_path: Path) -> Path:
    """A vault with hidden, symlinked, and non-Markdown entries plus a sentinel."""
    root = tmp_path / "vault"
    root.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("OUTSIDE SENTINEL\n", encoding="utf-8")
    _write(root, "visible.md", "visible\n")
    _write(root, ".hidden.md", "private\n")
    _write(root, ".obsidian/config.md", "private\n")
    _write(root, "asset.txt", "text\n")
    (root / "folder.md").mkdir()
    (root / "outside-link.md").symlink_to(outside)
    (root / "inside-link.md").symlink_to(root / "visible.md")
    return root


@pytest.mark.parametrize(
    ("path", "code"),
    [
        (".hidden.md", DomainErrorCode.ACCESS_DENIED),
        (".obsidian/config.md", DomainErrorCode.ACCESS_DENIED),
        ("outside-link.md", DomainErrorCode.ACCESS_DENIED),
        ("inside-link.md", DomainErrorCode.ACCESS_DENIED),
        ("asset.txt", DomainErrorCode.UNSUPPORTED_TYPE),
        ("folder.md", DomainErrorCode.NOT_A_FILE),
        ("missing.md", DomainErrorCode.NOT_FOUND),
        ("../outside.md", DomainErrorCode.INVALID_PATH),
    ],
)
def test_read_and_info_apply_policy(
    policy_vault: Path, path: str, code: DomainErrorCode
) -> None:
    """Read and info reject the same disallowed targets."""
    policy = PathPolicy(policy_vault)
    for operation in _both_operations(policy, path):
        with pytest.raises(KnowledgeError) as error:
            operation()
        assert error.value.code is code
        assert "OUTSIDE SENTINEL" not in str(error.value)
        assert str(policy_vault) not in str(error.value)


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (FileNotFoundError(errno.ENOENT, "gone"), DomainErrorCode.NOT_FOUND),
        (PermissionError(errno.EACCES, "denied"), DomainErrorCode.ACCESS_DENIED),
        (OSError(errno.ELOOP, "symlink"), DomainErrorCode.ACCESS_DENIED),
    ],
)
def test_open_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: OSError,
    code: DomainErrorCode,
) -> None:
    """Failures after policy checks map to safe domain errors."""
    _write(tmp_path, "note.md", "text\n")

    def failing_open(*args: object, **kwargs: object) -> int:
        raise failure

    monkeypatch.setattr(os, "open", failing_open)
    policy = PathPolicy(tmp_path)
    for operation in _both_operations(policy, "note.md"):
        with pytest.raises(KnowledgeError) as error:
            operation()
        assert error.value.code is code
        assert str(tmp_path) not in str(error.value)


def test_file_replaced_by_symlink_after_policy_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening does not follow a symlink swapped in after the policy check."""
    root = tmp_path / "vault"
    _write(root, "note.md", "text\n")
    outside = _write(tmp_path, "outside.md", "OUTSIDE SENTINEL\n")
    policy = PathPolicy(root)
    real_resolve = policy.resolve

    def swapping_resolve(api_path: str, target: TargetKind) -> ResolvedPath:
        resolved = real_resolve(api_path, target)
        (root / "note.md").unlink()
        (root / "note.md").symlink_to(outside)
        return resolved

    monkeypatch.setattr(policy, "resolve", swapping_resolve)
    with pytest.raises(KnowledgeError) as error:
        read_note(policy, ReadRequest(path="note.md"))
    assert error.value.code is DomainErrorCode.ACCESS_DENIED


def test_parent_replaced_by_symlink_after_policy_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Opening does not follow a parent directory swapped for a symlink."""
    root = tmp_path / "vault"
    _write(root, "dir/note.md", "inside\n")
    _write(tmp_path, "outside/note.md", "OUTSIDE SENTINEL\n")
    policy = PathPolicy(root)
    real_resolve = policy.resolve

    def swapping_resolve(api_path: str, target: TargetKind) -> ResolvedPath:
        resolved = real_resolve(api_path, target)
        (root / "dir").rename(root / "moved")
        (root / "dir").symlink_to(tmp_path / "outside")
        return resolved

    monkeypatch.setattr(policy, "resolve", swapping_resolve)
    with pytest.raises(KnowledgeError) as error:
        read_note(policy, ReadRequest(path="dir/note.md"))
    assert error.value.code is DomainErrorCode.ACCESS_DENIED


def test_close_errors_do_not_leak_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A close() error neither fails the read nor leaves descriptors open."""
    _write(tmp_path, "a/b/note.md", "text\n")
    policy = PathPolicy(tmp_path)
    real_close = os.close

    def failing_close(fd: int) -> None:
        real_close(fd)
        raise OSError(errno.EIO, "close failed")

    open_before = len(os.listdir("/proc/self/fd"))
    monkeypatch.setattr(os, "close", failing_close)
    result = read_note(policy, ReadRequest(path="a/b/note.md"))
    monkeypatch.undo()
    assert result.content == "text\n"
    assert len(os.listdir("/proc/self/fd")) == open_before


def test_file_replaced_by_fifo_after_policy_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A FIFO swapped in after the policy check is rejected without blocking."""
    _write(tmp_path, "note.md", "text\n")
    policy = PathPolicy(tmp_path)
    real_resolve = policy.resolve

    def swapping_resolve(api_path: str, target: TargetKind) -> ResolvedPath:
        resolved = real_resolve(api_path, target)
        (tmp_path / "note.md").unlink()
        os.mkfifo(tmp_path / "note.md")
        return resolved

    monkeypatch.setattr(policy, "resolve", swapping_resolve)
    with pytest.raises(KnowledgeError) as error:
        note_info(policy, InfoRequest(path="note.md"))
    assert error.value.code is DomainErrorCode.ACCESS_DENIED


# Info


def test_info_readable(tmp_path: Path) -> None:
    """Info derives the line count and hash from the loaded bytes."""
    data = b"\xef\xbb\xbfa\r\nb"
    path = _write(tmp_path, "note.md", data)
    result = note_info(PathPolicy(tmp_path), InfoRequest(path="note.md"))
    assert result.path == "note.md"
    assert result.size_bytes == len(data)
    assert result.line_count == 2
    assert result.content_sha256 == hashlib.sha256(data).hexdigest()
    assert result.readable is True
    assert result.unreadable_reason is None
    modified = datetime.fromisoformat(result.modified_at)
    assert result.modified_at.endswith("Z")
    assert modified.timestamp() == pytest.approx(path.stat().st_mtime, abs=1e-6)


def test_info_line_count_matches_read(tmp_path: Path) -> None:
    """Info and read agree on line counts, including a BOM-only file."""
    policy = PathPolicy(tmp_path)
    for index, data in enumerate([b"", b"\xef\xbb\xbf", b"\n", b"a\rb", b"a\nb\n"]):
        name = f"n{index}.md"
        _write(tmp_path, name, data)
        info = note_info(policy, InfoRequest(path=name))
        read = read_note(policy, ReadRequest(path=name))
        assert info.line_count == read.total_lines
        assert info.content_sha256 == read.content_sha256


def test_info_oversized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Oversized files report metadata without an unbounded read."""
    _write(tmp_path, "big.md", b"x" * 10_000)
    requested: list[int] = []
    real_read = os.read

    def tracking_read(fd: int, size: int) -> bytes:
        requested.append(size)
        return real_read(fd, size)

    monkeypatch.setattr(os, "read", tracking_read)
    result = note_info(PathPolicy(tmp_path), InfoRequest(path="big.md"), limits=SMALL)
    assert result.size_bytes == 10_000
    assert result.readable is False
    assert result.unreadable_reason == "too_large"
    assert result.line_count is None
    assert result.content_sha256 is None
    assert sum(requested) <= SMALL.max_file_bytes + 1


def test_info_invalid_text(tmp_path: Path) -> None:
    """Invalid text is reported as unreadable metadata, not an error."""
    _write(tmp_path, "bad.md", b"ok\x00\n")
    result = note_info(PathPolicy(tmp_path), InfoRequest(path="bad.md"))
    assert result.readable is False
    assert result.unreadable_reason == "invalid_text"
    assert result.line_count is None
    assert result.content_sha256 is None
    assert result.size_bytes == 4


def test_changes_are_reflected(tmp_path: Path) -> None:
    """Nothing is cached: a changed file is seen by the next call."""
    policy = PathPolicy(tmp_path)
    _write(tmp_path, "note.md", "first\n")
    before = read_note(policy, ReadRequest(path="note.md"))
    info_before = note_info(policy, InfoRequest(path="note.md"))
    _write(tmp_path, "note.md", "second\nline\n")
    after = read_note(policy, ReadRequest(path="note.md"))
    info_after = note_info(policy, InfoRequest(path="note.md"))
    assert after.content == "second\nline\n"
    assert after.content_sha256 != before.content_sha256
    assert info_after.content_sha256 == after.content_sha256
    assert info_after.content_sha256 != info_before.content_sha256
    assert info_after.line_count == 2


# List


@pytest.fixture
def list_vault(tmp_path: Path) -> Path:
    """A vault whose root mixes visible and excluded entries."""
    _write(tmp_path, "b.md", "b\n")
    _write(tmp_path, "A.md", "a\n")
    _write(tmp_path, "a.md", "a\n")
    _write(tmp_path, "C.MD", "c\n")
    _write(tmp_path, "notes.txt", "text\n")
    _write(tmp_path, ".hidden.md", "private\n")
    _write(tmp_path, ".git/config.md", "private\n")
    _write(tmp_path, "Sub dir/inner.md", "inner\n")
    (tmp_path / "Empty").mkdir()
    (tmp_path / "link.md").symlink_to(tmp_path / "a.md")
    _write(tmp_path, ".gitignore", "a.md\n")
    return tmp_path


def test_list_filters_and_sorts(list_vault: Path) -> None:
    """Policy applies before case-sensitive code-point ordering."""
    result = list_directory(PathPolicy(list_vault), ListRequest())
    assert result.path == ""
    assert [(entry.path, entry.kind) for entry in result.entries] == [
        ("A.md", "file"),
        ("C.MD", "file"),
        ("Empty", "directory"),
        ("Sub dir", "directory"),
        ("a.md", "file"),
        ("b.md", "file"),
    ]
    assert result.next_offset is None
    assert result.truncated is False


def test_list_subdirectory(list_vault: Path) -> None:
    """Entries are root-relative, and a trailing slash is normalized."""
    result = list_directory(PathPolicy(list_vault), ListRequest(path="Sub dir/"))
    assert result.path == "Sub dir"
    assert [entry.path for entry in result.entries] == ["Sub dir/inner.md"]


@pytest.mark.parametrize(
    ("offset", "limit", "paths", "next_offset"),
    [
        (0, 3, ["A.md", "C.MD", "Empty"], 3),
        (3, 3, ["Sub dir", "a.md", "b.md"], None),
        (2, 2, ["Empty", "Sub dir"], 4),
        (5, 3, ["b.md"], None),
        (6, 3, [], None),
        (100, 1, [], None),
        (0, 1, ["A.md"], 1),
    ],
)
def test_list_pagination(
    list_vault: Path,
    offset: int,
    limit: int,
    paths: list[str],
    next_offset: int | None,
) -> None:
    """Offset and limit select stable pages of the filtered listing."""
    result = list_directory(
        PathPolicy(list_vault), ListRequest(offset=offset, limit=limit), limits=SMALL
    )
    assert [entry.path for entry in result.entries] == paths
    assert result.next_offset == next_offset
    assert result.truncated is (next_offset is not None)


def test_list_limit_over_injected_maximum_is_invalid(list_vault: Path) -> None:
    """Operations enforce the injected directory-page maximum."""
    with pytest.raises(KnowledgeError) as error:
        list_directory(PathPolicy(list_vault), ListRequest(limit=4), limits=SMALL)
    assert error.value.code is DomainErrorCode.INVALID_ARGUMENT


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("a.md", DomainErrorCode.NOT_A_DIRECTORY),
        ("notes.txt", DomainErrorCode.NOT_A_DIRECTORY),
        (".git", DomainErrorCode.ACCESS_DENIED),
        ("Missing", DomainErrorCode.NOT_FOUND),
    ],
)
def test_list_errors(list_vault: Path, path: str, code: DomainErrorCode) -> None:
    """Listing requires a visible directory."""
    with pytest.raises(KnowledgeError) as error:
        list_directory(PathPolicy(list_vault), ListRequest(path=path))
    assert error.value.code is code


def test_list_entry_limit(list_vault: Path) -> None:
    """Directories with too many entries fail instead of being listed."""
    policy = PathPolicy(list_vault, immediate_entry_limit=3)
    with pytest.raises(KnowledgeError) as error:
        list_directory(policy, ListRequest())
    assert error.value.code is DomainErrorCode.DIRECTORY_LIMIT_EXCEEDED


# Read-only behavior


def _snapshot(root: Path) -> dict[str, tuple[str, bytes | str]]:
    snapshot: dict[str, tuple[str, bytes | str]] = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        if path.is_symlink():
            snapshot[key] = ("link", os.readlink(path))
        elif path.is_dir():
            snapshot[key] = ("dir", "")
        else:
            snapshot[key] = ("file", path.read_bytes())
    return snapshot


def test_operations_do_not_change_the_vault(policy_vault: Path) -> None:
    """All operations leave file bytes and directory contents unchanged."""
    _write(policy_vault, "big.md", b"x" * 100)
    _write(policy_vault, "bad.md", b"\xff\n")
    before = _snapshot(policy_vault)
    policy = PathPolicy(policy_vault)
    for path in ["visible.md", "big.md", "bad.md", ".hidden.md", "outside-link.md"]:
        for operation in _both_operations(policy, path, SMALL):
            try:
                operation()
            except KnowledgeError:
                pass
    list_directory(policy, ListRequest())
    assert _snapshot(policy_vault) == before
