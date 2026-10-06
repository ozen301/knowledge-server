"""Tests for the write proposals: new notes and edits saved in the inbox."""

import hashlib
import os
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from knowledge_server.core.limits import DEFAULT_LIMITS, Limits
from knowledge_server.core.models import (
    DomainErrorCode,
    InfoRequest,
    KnowledgeError,
    ListRequest,
    ProposeEditRequest,
    ProposeNoteRequest,
    ReadRequest,
    TextEdit,
    invalid_argument_message,
)
from knowledge_server.core.paths import PathPolicy
from knowledge_server.core.reader import list_directory, note_info, read_note
from knowledge_server.core.writer import propose_edit, propose_note

NOTE = "# Router\n\n- **RAM:** 16 GB\n- **Disk:** 1 TB\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A vault with one note, a symlinked directory, and an empty inbox."""
    root = tmp_path / "vault"
    (root / "homelab").mkdir(parents=True)
    (root / "homelab" / "router.md").write_bytes(NOTE.encode())
    (root / "inbox").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "linked").symlink_to(outside)
    return root


@pytest.fixture
def policy(root: Path) -> PathPolicy:
    """A policy for the vault fixture."""
    return PathPolicy(root)


def _outside_inbox(root: Path) -> dict[str, bytes]:
    """Record every file outside the inbox, to show that writes leave it alone."""
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not path.is_symlink()
        and path.relative_to(root).parts[0] != "inbox"
    }


def _inbox_files(root: Path) -> set[str]:
    return {
        str(path.relative_to(root / "inbox"))
        for path in (root / "inbox").rglob("*")
        if path.is_file()
    }


def _code(error: pytest.ExceptionInfo[KnowledgeError]) -> DomainErrorCode:
    return error.value.code


def _edit(
    policy: PathPolicy,
    path: str,
    edits: list[tuple[str, str]],
    base: str | None = None,
    limits: Limits = DEFAULT_LIMITS,
) -> str:
    """Propose an edit against the current bytes unless a hash is given."""
    if base is None:
        base = _sha((policy.root / path).read_bytes())
    request = ProposeEditRequest(
        path=path,
        base_sha256=base,
        edits=[TextEdit(old=old, new=new) for old, new in edits],
    )
    result = propose_edit(policy, request, limits)
    assert result.path == (path if path.startswith("inbox/") else f"inbox/{path}")
    return result.content_sha256


# New notes


def test_new_note_is_written_to_the_inbox_only(root: Path, policy: PathPolicy) -> None:
    """A new note goes to inbox/<path>, with its directories, and nowhere else."""
    before = _outside_inbox(root)
    result = propose_note(
        policy, ProposeNoteRequest(path="plans/new/plan.md", content="# Plan\n")
    )
    written = root / "inbox" / "plans" / "new" / "plan.md"
    assert written.read_bytes() == b"# Plan\n"
    assert result.path == "inbox/plans/new/plan.md"
    assert result.content_sha256 == _sha(b"# Plan\n")
    assert _outside_inbox(root) == before
    assert not (root / "plans").exists()
    assert _inbox_files(root) == {"plans/new/plan.md"}


def test_new_note_may_be_empty(policy: PathPolicy) -> None:
    """Empty content is a valid note."""
    result = propose_note(policy, ProposeNoteRequest(path="empty.md", content=""))
    assert (policy.root / "inbox" / "empty.md").read_bytes() == b""
    assert result.content_sha256 == _sha(b"")


def test_new_note_never_overwrites(root: Path, policy: PathPolicy) -> None:
    """An entry in the vault or the inbox, even a directory, blocks a new note."""
    with pytest.raises(KnowledgeError) as vault_error:
        propose_note(policy, ProposeNoteRequest(path="homelab/router.md", content="x"))
    propose_note(policy, ProposeNoteRequest(path="idea.md", content="first"))
    with pytest.raises(KnowledgeError) as inbox_error:
        propose_note(policy, ProposeNoteRequest(path="idea.md", content="second"))
    (root / "dir.md").mkdir()
    with pytest.raises(KnowledgeError) as directory_error:
        propose_note(policy, ProposeNoteRequest(path="dir.md", content="x"))
    assert _code(vault_error) is DomainErrorCode.ALREADY_EXISTS
    assert _code(inbox_error) is DomainErrorCode.ALREADY_EXISTS
    assert _code(directory_error) is DomainErrorCode.ALREADY_EXISTS
    assert (root / "inbox" / "idea.md").read_text() == "first"


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("inbox/idea.md", DomainErrorCode.INVALID_PATH),
        ("../idea.md", DomainErrorCode.INVALID_PATH),
        ("/idea.md", DomainErrorCode.INVALID_PATH),
        ("plans//idea.md", DomainErrorCode.INVALID_PATH),
        (".hidden/idea.md", DomainErrorCode.ACCESS_DENIED),
        ("plans/idea.txt", DomainErrorCode.UNSUPPORTED_TYPE),
        ("linked/idea.md", DomainErrorCode.ACCESS_DENIED),
        ("homelab/router.md/idea.md", DomainErrorCode.NOT_A_DIRECTORY),
    ],
)
def test_new_note_path_policy(
    root: Path, policy: PathPolicy, path: str, code: DomainErrorCode
) -> None:
    """New-note paths follow the common policy and may not name the inbox."""
    with pytest.raises(KnowledgeError) as error:
        propose_note(policy, ProposeNoteRequest(path=path, content="x"))
    assert _code(error) is code
    assert _inbox_files(root) == set()
    assert not any((root / "linked").iterdir())


def test_new_note_rejects_a_symlink_in_the_inbox(
    root: Path, policy: PathPolicy, tmp_path: Path
) -> None:
    """A symlink inside the inbox cannot redirect a write."""
    (root / "inbox" / "plans").symlink_to(tmp_path / "outside")
    with pytest.raises(KnowledgeError) as error:
        propose_note(policy, ProposeNoteRequest(path="plans/idea.md", content="x"))
    assert _code(error) is DomainErrorCode.ACCESS_DENIED
    assert not any((tmp_path / "outside").iterdir())


def test_new_note_removes_a_leftover_base_copy(root: Path, policy: PathPolicy) -> None:
    """A new note never inherits the base copy of an earlier edit."""
    leftover = root / "inbox" / ".base" / "old.md"
    leftover.parent.mkdir()
    leftover.write_text("old base")
    propose_note(policy, ProposeNoteRequest(path="old.md", content="new"))
    assert not leftover.exists()
    assert _inbox_files(root) == {"old.md"}


def test_new_note_result_size_limit(root: Path, policy: PathPolicy) -> None:
    """A new note must fit the readable file-size limit."""
    limits = replace(DEFAULT_LIMITS, max_file_bytes=4)
    propose_note(policy, ProposeNoteRequest(path="fits.md", content="abcd"), limits)
    with pytest.raises(KnowledgeError) as error:
        propose_note(policy, ProposeNoteRequest(path="big.md", content="abcde"), limits)
    assert _code(error) is DomainErrorCode.FILE_TOO_LARGE
    assert _inbox_files(root) == {"fits.md"}


# Edits


def test_first_edit_keeps_a_base_copy(root: Path, policy: PathPolicy) -> None:
    """The first edit writes the base copy and the proposal, not the vault note."""
    before = _outside_inbox(root)
    digest = _edit(policy, "homelab/router.md", [("16 GB", "32 GB ECC")])
    proposal = root / "inbox" / "homelab" / "router.md"
    assert proposal.read_text() == NOTE.replace("16 GB", "32 GB ECC")
    assert (root / "inbox" / ".base" / "homelab" / "router.md").read_text() == NOTE
    assert digest == _sha(proposal.read_bytes())
    assert _outside_inbox(root) == before
    assert _inbox_files(root) == {".base/homelab/router.md", "homelab/router.md"}


def test_later_edits_change_the_proposal_and_keep_the_base(
    root: Path, policy: PathPolicy
) -> None:
    """Editing the inbox path changes the proposal and keeps its base copy."""
    digest = _edit(policy, "homelab/router.md", [("16 GB", "32 GB")])
    _edit(policy, "inbox/homelab/router.md", [("1 TB", "2 TB")], base=digest)
    proposal = root / "inbox" / "homelab" / "router.md"
    assert proposal.read_text() == NOTE.replace("16 GB", "32 GB").replace(
        "1 TB", "2 TB"
    )
    assert (root / "inbox" / ".base" / "homelab" / "router.md").read_text() == NOTE


def test_vault_note_with_a_pending_proposal_is_refused(
    root: Path, policy: PathPolicy
) -> None:
    """A vault path with a pending proposal must be edited through the inbox."""
    _edit(policy, "homelab/router.md", [("16 GB", "32 GB")])
    propose_note(policy, ProposeNoteRequest(path="idea.md", content="x"))
    with pytest.raises(KnowledgeError) as pending:
        _edit(policy, "homelab/router.md", [("1 TB", "2 TB")])
    with pytest.raises(KnowledgeError) as new_note:
        _edit(policy, "idea.md", [("x", "y")], base=_sha(b"x"))
    assert _code(pending) is DomainErrorCode.PROPOSAL_PENDING
    assert _code(new_note) is DomainErrorCode.PROPOSAL_PENDING
    assert "inbox/" in pending.value.message
    assert "router" not in pending.value.message


def test_new_note_proposal_has_no_base_copy(root: Path, policy: PathPolicy) -> None:
    """Editing a new-note proposal creates no base copy."""
    propose_note(policy, ProposeNoteRequest(path="idea.md", content="one\n"))
    _edit(policy, "inbox/idea.md", [("one", "two")])
    assert (root / "inbox" / "idea.md").read_text() == "two\n"
    assert _inbox_files(root) == {"idea.md"}


def test_stale_hash_writes_nothing(root: Path, policy: PathPolicy) -> None:
    """A hash that does not match the current bytes is refused."""
    with pytest.raises(KnowledgeError) as error:
        _edit(policy, "homelab/router.md", [("16 GB", "32 GB")], base="0" * 64)
    assert _code(error) is DomainErrorCode.STALE_CONTENT
    assert _inbox_files(root) == set()


@pytest.mark.parametrize(
    ("edits", "code", "position"),
    [
        ([("missing", "x")], DomainErrorCode.TEXT_NOT_FOUND, 1),
        ([("16 GB", "x"), ("- **", "x")], DomainErrorCode.TEXT_NOT_UNIQUE, 2),
        ([("16 GB", "1 TB"), ("1 TB", "x")], DomainErrorCode.TEXT_NOT_UNIQUE, 2),
        ([("16 GB", "x"), ("16 GB", "y")], DomainErrorCode.TEXT_NOT_FOUND, 2),
    ],
)
def test_text_to_replace_must_occur_exactly_once(
    root: Path,
    policy: PathPolicy,
    edits: list[tuple[str, str]],
    code: DomainErrorCode,
    position: int,
) -> None:
    """Each edit sees the result of the earlier ones; a failure writes nothing."""
    with pytest.raises(KnowledgeError) as error:
        _edit(policy, "homelab/router.md", edits)
    assert _code(error) is code
    assert error.value.message.startswith(f"Edit {position}:")
    assert _inbox_files(root) == set()


def test_overlapping_occurrences_count(root: Path, policy: PathPolicy) -> None:
    """An overlapping second occurrence makes a text not unique."""
    (root / "aaa.md").write_text("aaa\n")
    with pytest.raises(KnowledgeError) as error:
        _edit(policy, "aaa.md", [("aa", "b")])
    assert _code(error) is DomainErrorCode.TEXT_NOT_UNIQUE


def test_edits_apply_in_order(root: Path, policy: PathPolicy) -> None:
    """Each edit applies to the text that the earlier edits produced."""
    _edit(
        policy,
        "homelab/router.md",
        [("16 GB", "32 GB"), ("32 GB", "64 GB"), ("\n- **Disk:** 1 TB", "")],
    )
    assert (root / "inbox" / "homelab" / "router.md").read_text() == (
        "# Router\n\n- **RAM:** 64 GB\n"
    )


def test_bom_and_crlf_are_preserved(root: Path, policy: PathPolicy) -> None:
    """An edit keeps the note's BOM and CRLF line endings."""
    raw = b"\xef\xbb\xbfone\r\ntwo\r\n"
    (root / "windows.md").write_bytes(raw)
    _edit(policy, "windows.md", [("one\ntwo", "one\nnew\ntwo")])
    assert (root / "inbox" / "windows.md").read_bytes() == (
        b"\xef\xbb\xbfone\r\nnew\r\ntwo\r\n"
    )
    assert (root / "inbox" / ".base" / "windows.md").read_bytes() == raw


def test_lone_cr_survives_a_crlf_round_trip(root: Path, policy: PathPolicy) -> None:
    """A lone CR, a character within a line, is written back unchanged."""
    (root / "cr.md").write_bytes(b"a\rb\r\r\nc\r\n")
    _edit(policy, "cr.md", [("c", "d")])
    assert (root / "inbox" / "cr.md").read_bytes() == b"a\rb\r\r\nd\r\n"


def test_mixed_line_endings_are_refused(root: Path, policy: PathPolicy) -> None:
    """A note with both CRLF and LF endings cannot be edited."""
    (root / "mixed.md").write_bytes(b"one\r\ntwo\n")
    with pytest.raises(KnowledgeError) as error:
        _edit(policy, "mixed.md", [("one", "x")])
    assert _code(error) is DomainErrorCode.MIXED_LINE_ENDINGS
    assert _inbox_files(root) == set()


def test_missing_final_newline_stays_missing(root: Path, policy: PathPolicy) -> None:
    """Edits do not add a final newline unless the new text does."""
    (root / "open.md").write_bytes(b"one\ntwo")
    _edit(policy, "open.md", [("two", "three")])
    assert (root / "inbox" / "open.md").read_bytes() == b"one\nthree"
    _edit(policy, "inbox/open.md", [("three", "three\n")])
    assert (root / "inbox" / "open.md").read_bytes() == b"one\nthree\n"


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("homelab/missing.md", DomainErrorCode.NOT_FOUND),
        ("inbox/missing.md", DomainErrorCode.NOT_FOUND),
        ("inbox/.base/homelab/router.md", DomainErrorCode.ACCESS_DENIED),
        ("homelab", DomainErrorCode.UNSUPPORTED_TYPE),
        ("linked/note.md", DomainErrorCode.ACCESS_DENIED),
        ("../router.md", DomainErrorCode.INVALID_PATH),
    ],
)
def test_edit_path_policy(
    root: Path, policy: PathPolicy, path: str, code: DomainErrorCode
) -> None:
    """Edit paths follow the common policy; base copies are hidden."""
    with pytest.raises(KnowledgeError) as error:
        _edit(policy, path, [("a", "b")], base="0" * 64)
    assert _code(error) is code


def test_edit_checks_the_vault_path_before_a_pending_proposal(
    root: Path, policy: PathPolicy
) -> None:
    """An invalid vault path is reported even if a proposal exists for it."""
    for path, code in (
        ("C:/note.md", DomainErrorCode.INVALID_PATH),
        ("linked/note.md", DomainErrorCode.ACCESS_DENIED),
        ("homelab/router.md/note.md", DomainErrorCode.NOT_A_DIRECTORY),
    ):
        proposal = root / "inbox" / path
        proposal.parent.mkdir(parents=True, exist_ok=True)
        proposal.write_text("x")
        with pytest.raises(KnowledgeError) as error:
            _edit(policy, path, [("x", "y")], base=_sha(b"x"))
        assert _code(error) is code


def test_symlinked_base_directory_is_refused(
    root: Path, policy: PathPolicy, tmp_path: Path
) -> None:
    """A symlink in place of the base directory cannot redirect a base copy."""
    (root / "inbox" / ".base").symlink_to(tmp_path / "outside")
    with pytest.raises(KnowledgeError) as error:
        _edit(policy, "homelab/router.md", [("16 GB", "32 GB")])
    assert _code(error) is DomainErrorCode.ACCESS_DENIED
    assert not any((tmp_path / "outside").iterdir())
    assert not (root / "inbox" / "homelab").exists()


def test_unreadable_notes_cannot_be_edited(root: Path, policy: PathPolicy) -> None:
    """Oversized or invalid notes return the read errors."""
    (root / "binary.md").write_bytes(b"a\x00b")
    (root / "big.md").write_bytes(b"12345")
    limits = replace(DEFAULT_LIMITS, max_file_bytes=4)
    with pytest.raises(KnowledgeError) as invalid:
        _edit(policy, "binary.md", [("a", "b")])
    with pytest.raises(KnowledgeError) as large:
        _edit(policy, "big.md", [("1", "0")], limits=limits)
    assert _code(invalid) is DomainErrorCode.INVALID_ENCODING
    assert _code(large) is DomainErrorCode.FILE_TOO_LARGE


def test_edit_result_size_limit(root: Path, policy: PathPolicy) -> None:
    """The edited bytes, including a BOM, must fit the file-size limit."""
    (root / "small.md").write_bytes(b"\xef\xbb\xbfa")
    limits = replace(DEFAULT_LIMITS, max_file_bytes=4)
    with pytest.raises(KnowledgeError) as error:
        _edit(policy, "small.md", [("a", "ab")], limits=limits)
    assert _code(error) is DomainErrorCode.FILE_TOO_LARGE
    assert _inbox_files(root) == set()


def test_injected_text_and_edit_limits(root: Path, policy: PathPolicy) -> None:
    """The core applies injected text and edit limits."""
    limits = replace(DEFAULT_LIMITS, max_write_text_bytes=4, max_edits=1)
    propose_note(policy, ProposeNoteRequest(path="ok.md", content="é!!"), limits)
    for call in (
        lambda: propose_note(
            policy, ProposeNoteRequest(path="no.md", content="éé!"), limits
        ),
        lambda: _edit(policy, "homelab/router.md", [("16 ", "32")], limits=limits),
        lambda: _edit(
            policy, "homelab/router.md", [("6", "8"), ("T", "G")], limits=limits
        ),
    ):
        with pytest.raises(KnowledgeError) as error:
            call()
        assert _code(error) is DomainErrorCode.INVALID_ARGUMENT
    assert _inbox_files(root) == {"ok.md"}


def test_no_temporary_files_remain(root: Path, policy: PathPolicy) -> None:
    """Successful and failed writes leave no temporary files."""
    propose_note(policy, ProposeNoteRequest(path="a.md", content="a\n"))
    _edit(policy, "homelab/router.md", [("16 GB", "32 GB")])
    with pytest.raises(KnowledgeError):
        propose_note(policy, ProposeNoteRequest(path="a.md", content="b\n"))
    names = {path.name for path in (root / "inbox").rglob("*")}
    assert names == {"a.md", ".base", "homelab", "router.md"}


def test_proposals_are_readable_and_base_copies_hidden(
    root: Path, policy: PathPolicy
) -> None:
    """The read tools serve proposals with the hash an edit needs."""
    digest = _edit(policy, "homelab/router.md", [("16 GB", "32 GB")])
    read = read_note(policy, ReadRequest(path="inbox/homelab/router.md"))
    assert read.content_sha256 == digest
    assert "32 GB" in read.numbered_content
    assert note_info(policy, InfoRequest(path="inbox/homelab/router.md")).readable
    listing = list_directory(policy, ListRequest(path="inbox"))
    assert [entry.path for entry in listing.entries] == ["inbox/homelab"]
    with pytest.raises(KnowledgeError) as error:
        read_note(policy, ReadRequest(path="inbox/.base/homelab/router.md"))
    assert _code(error) is DomainErrorCode.ACCESS_DENIED


def test_files_are_readable_by_other_accounts(root: Path, policy: PathPolicy) -> None:
    """The vault owner reviews proposals without sudo under the default umask."""
    previous = os.umask(0o022)
    try:
        _edit(policy, "homelab/router.md", [("16 GB", "32 GB")])
    finally:
        os.umask(previous)
    for path in (root / "inbox").rglob("*"):
        assert path.stat().st_mode & 0o004


# Request validation


@pytest.mark.parametrize("text", ["a\x00b", "a\rb", "﻿a", "a﻿b"])
def test_text_must_not_contain_nul_cr_or_bom(text: str) -> None:
    """Text with NUL, CR, or U+FEFF is rejected without echoing it."""
    for model, arguments in (
        (ProposeNoteRequest, {"path": "a.md", "content": text}),
        (
            ProposeEditRequest,
            {
                "path": "a.md",
                "base_sha256": "0" * 64,
                "edits": [{"old": text, "new": ""}],
            },
        ),
        (
            ProposeEditRequest,
            {
                "path": "a.md",
                "base_sha256": "0" * 64,
                "edits": [{"old": "a", "new": text}],
            },
        ),
    ):
        with pytest.raises(ValidationError) as error:
            model.model_validate(arguments)
        message = invalid_argument_message(model, error.value)
        assert "NUL, CR, or U+FEFF" in message
        assert text not in message


@pytest.mark.parametrize(
    ("arguments", "named"),
    [
        ({"base_sha256": "A" * 64}, "base_sha256"),
        ({"base_sha256": "0" * 63}, "base_sha256"),
        ({"edits": []}, "edits"),
        (
            {"edits": [{"old": "a", "new": "b"}] * (DEFAULT_LIMITS.max_edits + 1)},
            "edits",
        ),
        ({"edits": [{"old": "", "new": "b"}]}, "edits"),
        ({"edits": [{"old": "a"}]}, "edits"),
        ({"edits": [{"old": "a", "new": "b", "extra": 1}]}, "edits"),
    ],
)
def test_edit_request_bounds(arguments: dict[str, object], named: str) -> None:
    """Malformed hashes and edit lists name the rejected argument."""
    valid = {
        "path": "a.md",
        "base_sha256": "0" * 64,
        "edits": [{"old": "a", "new": ""}],
    }
    with pytest.raises(ValidationError) as error:
        ProposeEditRequest.model_validate(valid | arguments)
    assert named in invalid_argument_message(ProposeEditRequest, error.value)


def test_request_text_limit_counts_utf8_bytes() -> None:
    """The text limit counts UTF-8 bytes across all texts of a request."""
    limit = DEFAULT_LIMITS.max_write_text_bytes
    ProposeNoteRequest(path="a.md", content="a" * limit)
    with pytest.raises(ValidationError):
        ProposeNoteRequest(path="a.md", content="é" * (limit // 2 + 1))
    half = "a" * (limit // 2)
    ProposeEditRequest(
        path="a.md", base_sha256="0" * 64, edits=[TextEdit(old=half, new=half)]
    )
    with pytest.raises(ValidationError) as error:
        ProposeEditRequest(
            path="a.md",
            base_sha256="0" * 64,
            edits=[TextEdit(old=half, new=half + "a")],
        )
    assert "256 KiB" in invalid_argument_message(ProposeEditRequest, error.value)
