"""Bounded note loading and the read, list, and info operations."""

import contextlib
import hashlib
import os
import stat
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
_READ_CHUNK_BYTES = 64 * 1024

type UnreadableReason = Literal["too_large", "invalid_text"]


@dataclass(frozen=True, slots=True)
class LoadedNote:
    """A note's metadata and, when readable, its lines from one bounded load.

    `lines` and `content_sha256` are None exactly when `unreadable_reason` is
    set. Lines exclude their LF terminators.
    """

    size_bytes: int
    modified_at: datetime
    lines: tuple[str, ...] | None
    content_sha256: str | None
    unreadable_reason: UnreadableReason | None


def load_note(
    policy: PathPolicy, resolved: ResolvedPath, limits: Limits = DEFAULT_LIMITS
) -> LoadedNote:
    """Load at most the file-size limit plus one byte from a resolved file.

    Raises `KnowledgeError` when the file cannot be opened as a regular file
    below the policy root. Oversized or invalid text is reported through
    `unreadable_reason` rather than raised, so metadata remains available.
    """
    fd = _open_below_root(policy.root, resolved.relative_path)
    try:
        status = os.fstat(fd)
        if stat.S_ISDIR(status.st_mode):
            raise KnowledgeError(DomainErrorCode.NOT_A_FILE)
        if not stat.S_ISREG(status.st_mode):
            raise KnowledgeError(DomainErrorCode.ACCESS_DENIED)
        raw = _read_bounded(fd, limits.max_file_bytes + 1)
    except OSError:
        raise KnowledgeError(DomainErrorCode.ACCESS_DENIED) from None
    finally:
        _close(fd)

    size_bytes = status.st_size
    modified_at = datetime.fromtimestamp(status.st_mtime, UTC)
    if len(raw) > limits.max_file_bytes:
        return LoadedNote(size_bytes, modified_at, None, None, "too_large")
    lines = _decode_lines(raw)
    if lines is None:
        return LoadedNote(size_bytes, modified_at, None, None, "invalid_text")
    digest = hashlib.sha256(raw).hexdigest()
    return LoadedNote(size_bytes, modified_at, lines, digest, None)


def read_note(
    policy: PathPolicy, request: ReadRequest, limits: Limits = DEFAULT_LIMITS
) -> ReadResult:
    """Return whole lines of a visible note within the line and byte limits."""
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
            content="",
            start_line=None,
            end_line=None,
            total_lines=total_lines,
            next_line=None,
            truncated=False,
            content_sha256=note.content_sha256,
        )

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
        parts.append(line)
        used_bytes += line_bytes
        end = number

    return ReadResult(
        path=resolved.relative_path,
        content="".join(parts),
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
    """Return one page of a visible directory's visible immediate children."""
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
    """Return metadata for a visible note, including oversized or invalid text."""
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


def _read_bounded(fd: int, max_bytes: int) -> bytes:
    """Read until EOF or `max_bytes`, whichever comes first."""
    chunks: list[bytes] = []
    remaining = max_bytes
    while remaining > 0:
        chunk = os.read(fd, min(remaining, _READ_CHUNK_BYTES))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _decode_lines(raw: bytes) -> tuple[str, ...] | None:
    """Split strict UTF-8 into LF-delimited lines, or return None if invalid."""
    if b"\x00" in raw:
        return None
    body = raw.removeprefix(_UTF8_BOM)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return None
    # Split on LF only. str.splitlines() would also split on lone CR and
    # Unicode separators, which ripgrep keeps within a line.
    text = text.replace("\r\n", "\n")
    if not text:
        return ()
    return tuple(text.removesuffix("\n").split("\n"))
