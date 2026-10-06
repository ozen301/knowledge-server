"""Bounded note loading and the read, list, and info operations.

Every operation checks the caller's path with `PathPolicy` before touching the
note. Read and info then load the note with `load_note`, search with
`load_note_text`, and edit proposals with `load_note_bytes`. All of them
read at most the file-size limit plus one byte, so an oversized file is
detected without reading all of it, and all apply the same content checks.
"""

import contextlib
import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from knowledge_server.core.limits import DEFAULT_LIMITS, Limits
from knowledge_server.core.models import (
    DomainErrorCode,
    InfoRequest,
    InfoResult,
    KnowledgeError,
    ListEntry,
    ListRequest,
    ListResult,
    ReadRequest,
    ReadResult,
)
from knowledge_server.core.paths import PathPolicy, ResolvedPath, TargetKind

_UTF8_BOM = b"\xef\xbb\xbf"
# Size of each os.read() call while loading a note.
_READ_CHUNK_BYTES = 64 * 1024

type UnreadableReason = Literal["too_large", "invalid_text"]


@dataclass(frozen=True, slots=True)
class LoadedNote:
    """A note's metadata and, when readable, its lines from one bounded load.

    Attributes:
        size_bytes: File size reported by the filesystem when the note was
            opened.
        modified_at: Last modification time, in UTC.
        lines: The note's lines without their LF terminators, with a leading
            BOM removed and CRLF normalized; None when the note is unreadable.
        content_sha256: SHA-256 of the loaded raw bytes; None when the note is
            unreadable.
        unreadable_reason: Why the note cannot be read, or None when it can.
            `lines` and `content_sha256` are None exactly when this is set.
    """

    size_bytes: int
    modified_at: datetime
    lines: tuple[str, ...] | None
    content_sha256: str | None
    unreadable_reason: UnreadableReason | None


@dataclass(frozen=True, slots=True)
class LoadedText:
    """A note's text from one bounded load, as search uses it.

    Attributes:
        text: The text with a leading BOM removed and CRLF normalized to LF;
            None when the note is unreadable.
        unreadable_reason: Why the note cannot be searched, or None when it
            can. `text` is None exactly when this is set.
    """

    text: str | None
    unreadable_reason: UnreadableReason | None


def load_note(
    policy: PathPolicy, resolved: ResolvedPath, limits: Limits = DEFAULT_LIMITS
) -> LoadedNote:
    """Load a checked note, reading at most the file-size limit plus one byte.

    An oversized or invalid-text note is not an error here: it is reported
    through `unreadable_reason`, so info can still return its metadata. Read
    turns that reason into an error.

    Args:
        policy: The policy that checked `resolved`.
        resolved: A file path returned by `policy.resolve`.
        limits: Limits that set the largest readable file.

    Returns:
        The note's metadata and, when readable, its lines and hash.

    Raises:
        KnowledgeError: `NOT_FOUND` if the file disappeared, `NOT_A_FILE` if
            it was replaced by a directory, or `ACCESS_DENIED` if it cannot be
            opened or read or is no longer a regular file.
    """
    status, raw = _load_raw(policy, resolved, limits.max_file_bytes + 1)
    size_bytes = status.st_size
    modified_at = datetime.fromtimestamp(status.st_mtime, UTC)
    if len(raw) > limits.max_file_bytes:
        return LoadedNote(size_bytes, modified_at, None, None, "too_large")
    lines = _decode_lines(raw)
    if lines is None:
        return LoadedNote(size_bytes, modified_at, None, None, "invalid_text")
    digest = hashlib.sha256(raw).hexdigest()
    return LoadedNote(size_bytes, modified_at, lines, digest, None)


def load_note_text(
    policy: PathPolicy,
    resolved: ResolvedPath,
    limits: Limits = DEFAULT_LIMITS,
    *,
    byte_budget: int,
    checkpoint: Callable[[], None] | None = None,
    on_read: Callable[[int], None] | None = None,
) -> LoadedText:
    """Load a checked note's text within a byte budget.

    The note is opened and checked as `load_note` does. It reads at most the
    file-size limit plus one byte, and at most `byte_budget` plus one byte, so
    a note that would exceed the budget is detected without reading on.

    Args:
        policy: The policy that checked `resolved`.
        resolved: A file path returned by `policy.resolve` or accepted by
            `policy.visible_child`.
        limits: Limits that set the largest readable file.
        byte_budget: Most raw bytes the caller can still accept.
        checkpoint: Called before each read chunk; it may raise to stop the
            load.
        on_read: Called with each chunk's size as soon as it is read, even if
            a later read fails; it may raise to stop the load.

    Returns:
        The note's text when it is readable, or why it is not.

    Raises:
        KnowledgeError: `SEARCH_LIMIT_EXCEEDED` if the note has more than
            `byte_budget` bytes, or the codes `load_note` raises.
    """
    max_bytes = min(limits.max_file_bytes, byte_budget) + 1
    _, raw = _load_raw(policy, resolved, max_bytes, checkpoint, on_read)
    if len(raw) > byte_budget:
        raise KnowledgeError(DomainErrorCode.SEARCH_LIMIT_EXCEEDED)
    if len(raw) > limits.max_file_bytes:
        return LoadedText(None, "too_large")
    text = _decode_text(raw)
    if text is None:
        return LoadedText(None, "invalid_text")
    return LoadedText(text, None)


def load_note_bytes(
    policy: PathPolicy, resolved: ResolvedPath, limits: Limits = DEFAULT_LIMITS
) -> tuple[bytes, str]:
    """Load a checked note's raw bytes and decoded text, as an edit needs them.

    Args:
        policy: The policy that checked `resolved`.
        resolved: A file path returned by `policy.resolve`.
        limits: Limits that set the largest readable file.

    Returns:
        The raw bytes, and the text as the read tools see it: without a
        leading BOM and with CRLF normalized to LF.

    Raises:
        KnowledgeError: `FILE_TOO_LARGE`, `INVALID_ENCODING`, or the codes
            `load_note` raises.
    """
    _, raw = _load_raw(policy, resolved, limits.max_file_bytes + 1)
    if len(raw) > limits.max_file_bytes:
        raise KnowledgeError(DomainErrorCode.FILE_TOO_LARGE)
    text = _decode_text(raw)
    if text is None:
        raise KnowledgeError(DomainErrorCode.INVALID_ENCODING)
    return raw, text


def read_note(
    policy: PathPolicy, request: ReadRequest, limits: Limits = DEFAULT_LIMITS
) -> ReadResult:
    """Read a range of whole lines from a note.

    Each returned line is prefixed with its line number and a tab. The read
    stops early, with `truncated` set, before a line that would make the line
    text exceed `limits.max_read_content_bytes`; the prefixes do not count.

    Args:
        policy: The policy for the vault root.
        request: The note path and line range.
        limits: Limits for the default and maximum range, content size, and
            file size.

    Returns:
        The lines and the position information for continuing the read.

    Raises:
        KnowledgeError: `INVALID_ARGUMENT` if the range is longer than
            `limits.max_read_lines`; `FILE_TOO_LARGE`; `INVALID_ENCODING`;
            `LINE_TOO_LONG` if the first requested line alone exceeds the
            content limit; or any code from the path checks and loading.
    """
    start = request.start_line
    if request.end_line is None:
        requested_end = start + limits.default_read_lines - 1
    elif request.end_line - start + 1 > limits.max_read_lines:
        raise KnowledgeError(DomainErrorCode.INVALID_ARGUMENT)
    else:
        requested_end = request.end_line

    resolved = policy.resolve(request.path, TargetKind.FILE)
    note = load_note(policy, resolved, limits)
    if note.unreadable_reason == "too_large":
        raise KnowledgeError(DomainErrorCode.FILE_TOO_LARGE)
    if note.lines is None or note.content_sha256 is None:
        raise KnowledgeError(DomainErrorCode.INVALID_ENCODING)

    lines = note.lines
    total_lines = len(lines)
    if start > total_lines:
        return ReadResult(
            path=resolved.relative_path,
            numbered_content="",
            start_line=None,
            end_line=None,
            total_lines=total_lines,
            next_line=None,
            truncated=False,
            content_sha256=note.content_sha256,
        )

    # Add whole lines until the range ends or the next line would exceed the
    # content limit. The limit counts the line text, not the number prefixes,
    # so the prefixes never change where a read stops. `end` is the last line
    # added so far.
    last_requested = min(requested_end, total_lines)
    parts: list[str] = []
    used_bytes = 0
    end = start - 1
    for number in range(start, last_requested + 1):
        line = lines[number - 1] + "\n"
        line_bytes = len(line.encode())
        if used_bytes + line_bytes > limits.max_read_content_bytes:
            if not parts:
                raise KnowledgeError(DomainErrorCode.LINE_TOO_LONG)
            break
        parts.append(f"{number}\t{line}")
        used_bytes += line_bytes
        end = number

    return ReadResult(
        path=resolved.relative_path,
        numbered_content="".join(parts),
        start_line=start,
        end_line=end,
        total_lines=total_lines,
        next_line=end + 1 if end < total_lines else None,
        truncated=end < last_requested,
        content_sha256=note.content_sha256,
    )


def list_directory(
    policy: PathPolicy, request: ListRequest, limits: Limits = DEFAULT_LIMITS
) -> ListResult:
    """List one page of a directory's visible immediate children.

    Pages are slices of the sorted listing. A caller gets the next page by
    passing the result's `next_offset` as the next request's `offset`.

    Args:
        policy: The policy for the vault root.
        request: The directory path, offset, and page size.
        limits: Limits for the largest page.

    Returns:
        The entries in the page and the offset of the next page.

    Raises:
        KnowledgeError: `INVALID_ARGUMENT` if `request.limit` is larger than
            `limits.max_directory_page`, `DIRECTORY_LIMIT_EXCEEDED`, or any
            code from the path checks.
    """
    if request.limit > limits.max_directory_page:
        raise KnowledgeError(DomainErrorCode.INVALID_ARGUMENT)
    directory = policy.resolve(request.path, TargetKind.DIRECTORY)
    entries = policy.discover_immediate(directory.relative_path)
    page_end = request.offset + request.limit
    next_offset = page_end if page_end < len(entries) else None
    return ListResult(
        path=directory.relative_path,
        entries=[
            ListEntry(
                path=entry.relative_path,
                kind="directory" if entry.kind is TargetKind.DIRECTORY else "file",
            )
            for entry in entries[request.offset : page_end]
        ],
        next_offset=next_offset,
        truncated=next_offset is not None,
    )


def note_info(
    policy: PathPolicy, request: InfoRequest, limits: Limits = DEFAULT_LIMITS
) -> InfoResult:
    """Return metadata for a note, including one too large or invalid to read.

    Args:
        policy: The policy for the vault root.
        request: The note path.
        limits: Limits that set the largest readable file.

    Returns:
        The note's size, modification time, and, when readable, its line count
        and hash.

    Raises:
        KnowledgeError: Any code from the path checks and loading.
    """
    resolved = policy.resolve(request.path, TargetKind.FILE)
    note = load_note(policy, resolved, limits)
    return InfoResult(
        path=resolved.relative_path,
        size_bytes=note.size_bytes,
        modified_at=note.modified_at.isoformat().replace("+00:00", "Z"),
        line_count=None if note.lines is None else len(note.lines),
        content_sha256=note.content_sha256,
        readable=note.unreadable_reason is None,
        unreadable_reason=note.unreadable_reason,
    )


def _load_raw(
    policy: PathPolicy,
    resolved: ResolvedPath,
    max_bytes: int,
    checkpoint: Callable[[], None] | None = None,
    on_read: Callable[[int], None] | None = None,
) -> tuple[os.stat_result, bytes]:
    """Open a checked regular file and read at most `max_bytes` from it."""
    fd = _open_below_root(policy.root, resolved.relative_path)
    try:
        status = os.fstat(fd)
        if stat.S_ISDIR(status.st_mode):
            raise KnowledgeError(DomainErrorCode.NOT_A_FILE)
        if not stat.S_ISREG(status.st_mode):
            raise KnowledgeError(DomainErrorCode.ACCESS_DENIED)
        raw = _read_bounded(fd, max_bytes, checkpoint, on_read)
    except OSError:
        raise KnowledgeError(DomainErrorCode.ACCESS_DENIED) from None
    finally:
        _close(fd)
    return status, raw


def _open_below_root(root: Path, relative_path: str) -> int:
    """Open a policy-checked file without following any symlink below the root.

    The path policy has already rejected symlinks and special files, but a file
    or directory can be replaced before the file is opened. Opening each
    component relative to its parent's descriptor with O_NOFOLLOW rejects a
    symlink anywhere in the path. O_NONBLOCK keeps a FIFO from blocking until
    the caller checks the file type.
    """
    *directories, name = relative_path.split("/")
    try:
        dir_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        raise KnowledgeError(DomainErrorCode.NOT_FOUND) from None
    except OSError:
        raise KnowledgeError(DomainErrorCode.ACCESS_DENIED) from None
    try:
        for directory in directories:
            child_fd = os.open(
                directory,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=dir_fd,
            )
            _close(dir_fd)
            dir_fd = child_fd
        return os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
    except FileNotFoundError:
        raise KnowledgeError(DomainErrorCode.NOT_FOUND) from None
    except OSError:
        raise KnowledgeError(DomainErrorCode.ACCESS_DENIED) from None
    finally:
        _close(dir_fd)


def _close(fd: int) -> None:
    """Close a descriptor, ignoring errors from the close itself.

    Linux releases the descriptor even when close() reports an error, so
    raising here would only hide the operation's result or leak a descriptor
    that the caller has not recorded yet.
    """
    with contextlib.suppress(OSError):
        os.close(fd)


def _read_bounded(
    fd: int,
    max_bytes: int,
    checkpoint: Callable[[], None] | None = None,
    on_read: Callable[[int], None] | None = None,
) -> bytes:
    """Read until EOF or `max_bytes`.

    `checkpoint` is called before each chunk and `on_read` with each chunk's
    size right after it is read; either may raise to stop reading.
    """
    chunks: list[bytes] = []
    remaining = max_bytes
    while remaining > 0:
        if checkpoint is not None:
            checkpoint()
        chunk = os.read(fd, min(remaining, _READ_CHUNK_BYTES))
        if not chunk:
            break
        if on_read is not None:
            on_read(len(chunk))
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _decode_text(raw: bytes) -> str | None:
    """Decode strict UTF-8 without a BOM and with CRLF as LF, or return None."""
    if b"\x00" in raw:
        return None
    body = raw.removeprefix(_UTF8_BOM)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text.replace("\r\n", "\n")


def _decode_lines(raw: bytes) -> tuple[str, ...] | None:
    """Split strict UTF-8 into LF-delimited lines, or return None if invalid."""
    text = _decode_text(raw)
    if text is None:
        return None
    # Split on LF only. str.splitlines() would also split on lone CR and
    # Unicode separators, which ripgrep keeps within a line.
    if not text:
        return ()
    return tuple(text.removesuffix("\n").split("\n"))
