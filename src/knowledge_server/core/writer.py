"""Write proposals: new notes and edits saved in the inbox.

The service never changes a note outside the inbox, the directory `inbox/`
directly under the root. The proposal for the vault path `P` is the note
`inbox/P`; the vault owner reviews it and merges it into the knowledge vault.
The first edit of a vault note also keeps a base copy of the note's bytes at
`inbox/.base/P`, which the hidden-name rule keeps out of every tool.

Each file is written as a hidden temporary file in its directory and then
moved into place, so readers never see a partial file. Directories are opened
one component at a time without following symlinks, so a symlink placed in
the inbox cannot redirect a write.
"""

import contextlib
import errno
import hashlib
import os
import secrets
import stat
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path

from knowledge_server.core.limits import DEFAULT_LIMITS, Limits
from knowledge_server.core.models import (
    DomainErrorCode,
    KnowledgeError,
    ProposalResult,
    ProposeEditRequest,
    ProposeNoteRequest,
    edit_error,
    write_text_bytes,
)
from knowledge_server.core.paths import PathPolicy, TargetKind
from knowledge_server.core.reader import load_note_bytes

INBOX = "inbox"
"""The inbox directory's name, directly under the root."""

_BASE = ".base"
_UTF8_BOM = b"\xef\xbb\xbf"
# A hash check and the write that depends on it must not interleave with
# another write, so every write of the process takes this lock.
_WRITE_LOCK = threading.Lock()


def propose_note(
    policy: PathPolicy, request: ProposeNoteRequest, limits: Limits = DEFAULT_LIMITS
) -> ProposalResult:
    """Save a new note as a proposal at `inbox/<path>`.

    The content is written as UTF-8 without a BOM. A base copy left at the
    same path by an earlier edit is deleted, so a proposal has a base copy
    exactly when it started from an edit of a vault note.

    Args:
        policy: The policy for the root, which contains the inbox.
        request: The vault path the note should have, and its content.
        limits: Limits for the request text and the note size.

    Returns:
        The proposal's root-relative path and the hash of its bytes.

    Raises:
        KnowledgeError: `INVALID_ARGUMENT` beyond the text limit;
            `INVALID_PATH` for a path under `inbox/`; `ALREADY_EXISTS` if an
            entry exists at the path in the vault or the inbox;
            `FILE_TOO_LARGE`; or the codes of the path checks.
    """
    _check_limits([request.content], 0, limits)
    path = request.path
    if path.split("/", 1)[0] == INBOX:
        raise KnowledgeError(DomainErrorCode.INVALID_PATH)
    data = request.content.encode()
    with _WRITE_LOCK:
        if policy.probe_file(path) or policy.probe_file(f"{INBOX}/{path}"):
            raise KnowledgeError(DomainErrorCode.ALREADY_EXISTS)
        if len(data) > limits.max_file_bytes:
            raise KnowledgeError(DomainErrorCode.FILE_TOO_LARGE)
        with _open_inbox(policy.root) as inbox:
            _remove(inbox, f"{_BASE}/{path}")
            try:
                _write(inbox, path, data, replace=False)
            except FileExistsError:
                raise KnowledgeError(DomainErrorCode.ALREADY_EXISTS) from None
    return _result(path, data)


def propose_edit(
    policy: PathPolicy, request: ProposeEditRequest, limits: Limits = DEFAULT_LIMITS
) -> ProposalResult:
    """Apply exact text replacements to a note and save the result as a proposal.

    For a vault note `P`, the first edit writes the base copy and then
    `inbox/P`; a path `inbox/P` replaces that proposal and keeps its base
    copy. The edits apply in order to the note's text as the read tools see
    it. The proposal keeps the note's BOM, and writes CRLF line endings if the
    note uses them.

    Args:
        policy: The policy for the root, which contains the inbox.
        request: The note, the hash of the bytes the caller read, and the
            edits.
        limits: Limits for the request text, the number of edits, and the
            note size.

    Returns:
        The proposal's root-relative path and the hash of its bytes.

    Raises:
        KnowledgeError: `INVALID_ARGUMENT` beyond the text or edit limit;
            `PROPOSAL_PENDING` for a vault note whose proposal exists;
            `STALE_CONTENT`; `MIXED_LINE_ENDINGS`; `TEXT_NOT_FOUND` or
            `TEXT_NOT_UNIQUE` naming the failing edit; `FILE_TOO_LARGE`; or
            the codes of the path checks and loading.
    """
    texts = [text for edit in request.edits for text in (edit.old, edit.new)]
    _check_limits(texts, len(request.edits), limits)
    path = request.path
    from_vault = path.split("/", 1)[0] != INBOX
    vault_path = path if from_vault else path.removeprefix(f"{INBOX}/")
    with _WRITE_LOCK:
        # Check the path itself before the inbox, so an invalid vault path
        # cannot report a pending proposal.
        policy.probe_file(path)
        if from_vault and policy.probe_file(f"{INBOX}/{path}"):
            raise KnowledgeError(DomainErrorCode.PROPOSAL_PENDING)
        resolved = policy.resolve(path, TargetKind.FILE)
        raw, text = load_note_bytes(policy, resolved, limits)
        if hashlib.sha256(raw).hexdigest() != request.base_sha256:
            raise KnowledgeError(DomainErrorCode.STALE_CONTENT)
        crlf = b"\r\n" in raw
        if crlf and raw.count(b"\n") != raw.count(b"\r\n"):
            raise KnowledgeError(DomainErrorCode.MIXED_LINE_ENDINGS)
        for position, edit in enumerate(request.edits, start=1):
            start = text.find(edit.old)
            if start < 0:
                raise edit_error(DomainErrorCode.TEXT_NOT_FOUND, position)
            # Searching from start + 1 also finds an overlapping occurrence.
            if text.find(edit.old, start + 1) >= 0:
                raise edit_error(DomainErrorCode.TEXT_NOT_UNIQUE, position)
            text = text[:start] + edit.new + text[start + len(edit.old) :]
        if crlf:
            text = text.replace("\n", "\r\n")
        bom = _UTF8_BOM if raw.startswith(_UTF8_BOM) else b""
        data = bom + text.encode()
        if len(data) > limits.max_file_bytes:
            raise KnowledgeError(DomainErrorCode.FILE_TOO_LARGE)
        with _open_inbox(policy.root) as inbox:
            if not from_vault:
                _write(inbox, vault_path, data, replace=True)
            else:
                _write(inbox, f"{_BASE}/{vault_path}", raw, replace=True)
                try:
                    _write(inbox, vault_path, data, replace=False)
                except FileExistsError:
                    raise KnowledgeError(DomainErrorCode.PROPOSAL_PENDING) from None
    return _result(vault_path, data)


def _check_limits(texts: Iterable[str], edits: int, limits: Limits) -> None:
    """Apply injected limits; the request models already apply the defaults."""
    if (
        write_text_bytes(texts) > limits.max_write_text_bytes
        or edits > limits.max_edits
    ):
        raise KnowledgeError(DomainErrorCode.INVALID_ARGUMENT)


def _result(vault_path: str, data: bytes) -> ProposalResult:
    return ProposalResult(
        path=f"{INBOX}/{vault_path}", content_sha256=hashlib.sha256(data).hexdigest()
    )


@contextlib.contextmanager
def _open_inbox(root: Path) -> Iterator[int]:
    """Open the inbox directory and translate filesystem errors while writing."""
    try:
        inbox = os.open(root / INBOX, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            yield inbox
        finally:
            _close(inbox)
    except PermissionError:
        raise KnowledgeError(DomainErrorCode.ACCESS_DENIED) from None
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise KnowledgeError(DomainErrorCode.ACCESS_DENIED) from None
        if error.errno == errno.ENOTDIR:
            raise KnowledgeError(DomainErrorCode.NOT_A_DIRECTORY) from None
        raise


def _open_directory(inbox: int, parts: list[str], *, create: bool) -> int:
    """Open a directory below the inbox without following symlinks."""
    fd = os.dup(inbox)
    try:
        for part in parts:
            if create:
                with contextlib.suppress(FileExistsError):
                    os.mkdir(part, dir_fd=fd)
            try:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                )
            except NotADirectoryError:
                # Linux reports a symlink opened this way as not a directory.
                if stat.S_ISLNK(os.lstat(part, dir_fd=fd).st_mode):
                    raise KnowledgeError(DomainErrorCode.ACCESS_DENIED) from None
                raise
            _close(fd)
            fd = child
    except BaseException:
        _close(fd)
        raise
    return fd


def _write(inbox: int, relative_path: str, data: bytes, *, replace: bool) -> None:
    """Write a file below the inbox through a temporary file.

    With `replace`, an existing file is replaced; otherwise an existing entry
    raises `FileExistsError` and is left unchanged.
    """
    *parents, name = relative_path.split("/")
    directory = _open_directory(inbox, parents, create=True)
    temporary = f".tmp-{secrets.token_hex(8)}"
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
            dir_fd=directory,
        )
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            _close(fd)
        if replace:
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        else:
            # A hard link fails if the target exists, unlike a rename.
            os.link(
                temporary,
                name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
    finally:
        # The file may already be in place, so a failed cleanup must not
        # report the write as failed; a leftover temporary file is hidden.
        with contextlib.suppress(OSError):
            os.unlink(temporary, dir_fd=directory)
        _close(directory)


def _remove(inbox: int, relative_path: str) -> None:
    """Delete a file below the inbox if it exists."""
    *parents, name = relative_path.split("/")
    try:
        directory = _open_directory(inbox, parents, create=False)
    except FileNotFoundError:
        return
    try:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name, dir_fd=directory)
    finally:
        _close(directory)


def _close(fd: int) -> None:
    """Close a descriptor, ignoring errors, as `reader._close` explains."""
    with contextlib.suppress(OSError):
        os.close(fd)
