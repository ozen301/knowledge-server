"""Offline proposal tests using invented Git repositories."""

import hashlib
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from knowledge_server.core.git import ProposalBranches
from knowledge_server.core.limits import DEFAULT_LIMITS, Limits
from knowledge_server.core.models import (
    DomainErrorCode,
    KnowledgeError,
    ProposalResult,
    ProposeEditRequest,
    ProposeNoteRequest,
    TextEdit,
    invalid_argument_message,
)
from knowledge_server.core.models import (
    ProposalReadRequest as ReadRequest,
)
from knowledge_server.core.paths import PathPolicy
from knowledge_server.core.writer import propose_edit, propose_note

type ProposalVault = tuple[Path, Path, Path, Callable[..., bytes]]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _store(
    vault: ProposalVault, *, subtree: bool = False, limits: Limits = DEFAULT_LIMITS
) -> ProposalBranches:
    checkout, state, remote, _ = vault
    return ProposalBranches(
        PathPolicy(checkout / "subtree" if subtree else checkout),
        str(remote),
        state_directory=state,
        limits=limits,
        ssh_command=None,
    )


def _note(
    store: ProposalBranches,
    path: str = "new.md",
    content: str = "new\n",
    summary: str = "Add a note",
    **selection: str,
) -> ProposalResult:
    return propose_note(
        store,
        ProposeNoteRequest(
            path=path,
            content=content,
            summary=summary,
            **(selection or {"topic": "test"}),
        ),
        store.limits,
    )


def _edit(
    store: ProposalBranches,
    *,
    path: str = "note.md",
    raw: bytes = b"one\ntwo\n",
    edits: list[TextEdit] | None = None,
    **selection: str,
) -> ProposalResult:
    return propose_edit(
        store,
        ProposeEditRequest(
            path=path,
            summary="Revise a note",
            base_sha256=_sha(raw),
            edits=edits or [TextEdit(old="one", new="three")],
            **(selection or {"topic": "test"}),
        ),
        store.limits,
    )


def test_new_note_and_revision_leave_checkout_unchanged(
    proposal_vault: ProposalVault,
) -> None:
    """A branch can add and revise several notes without writing to the checkout."""
    checkout, _, remote, git = proposal_vault
    before = git(checkout, "status", "--porcelain")
    store = _store(proposal_vault)
    note = _note(store, path="new/plan.md", summary=" 追加の計画 ")
    assert git(remote, "cat-file", "commit", note.proposal).split(b"\n\n", 1)[1] == (
        "Unreviewed proposal:  追加の計画 \n".encode()
    )
    assert note.path == "new/plan.md"
    assert note.proposal.startswith("proposal/test-")
    assert note.content_sha256 == _sha(b"new\n")
    assert git(remote, "show", f"{note.proposal}:new/plan.md") == b"new\n"
    edited = _edit(store, proposal=note.proposal)
    revised = _edit(
        store,
        proposal=note.proposal,
        raw=b"three\ntwo\n",
        edits=[TextEdit(old="two", new="four")],
    )
    assert revised.proposal == edited.proposal == note.proposal
    assert git(remote, "show", f"{note.proposal}:note.md") == b"three\nfour\n"
    assert git(remote, "cat-file", "commit", note.proposal).split(b"\n\n", 1)[1] == (
        b"Unreviewed proposal: Revise a note\n"
    )
    assert git(remote, "rev-list", "--count", f"main..{note.proposal}").strip() == b"3"
    assert git(checkout, "status", "--porcelain") == before
    assert (checkout / "note.md").read_bytes() == b"one\ntwo\n"
    assert not (checkout / "new").exists()


def test_new_branch_uses_head_not_working_tree(proposal_vault: ProposalVault) -> None:
    """New writes base their checks on committed bytes, including dirty notes."""
    checkout, _, remote, git = proposal_vault
    (checkout / "note.md").write_text("dirty")
    store = _store(proposal_vault)
    result = _edit(store)
    assert git(remote, "show", f"{result.proposal}:note.md") == b"three\ntwo\n"
    assert (checkout / "note.md").read_text() == "dirty"
    with pytest.raises(KnowledgeError, match="already exists") as error:
        _note(store, path="note.md")
    assert error.value.code == DomainErrorCode.ALREADY_EXISTS


def test_edit_preserves_executable_mode(proposal_vault: ProposalVault) -> None:
    """Edits retain the committed mode while new notes use mode 100644."""
    checkout, _, remote, git = proposal_vault
    (checkout / "note.md").chmod(0o755)
    git(checkout, "add", "note.md")
    git(checkout, "commit", "-m", "Executable note")
    store = _store(proposal_vault)
    note = _note(store)
    edited = _edit(store, proposal=note.proposal)
    assert (
        git(remote, "ls-tree", edited.proposal, "--", "note.md").split()[0] == b"100755"
    )
    assert (
        git(remote, "ls-tree", edited.proposal, "--", "new.md").split()[0] == b"100644"
    )


def test_subtree_prefix_and_listing(proposal_vault: ProposalVault) -> None:
    """Branches use checkout paths, while reads and listings use root paths."""
    _, _, remote, git = proposal_vault
    store = _store(proposal_vault, subtree=True)
    first = _note(store, path="計画/new.md")
    _edit(
        store,
        raw=b"subtree\n",
        edits=[TextEdit(old="subtree", new="changed")],
        proposal=first.proposal,
    )
    assert git(remote, "show", f"{first.proposal}:subtree/計画/new.md") == b"new\n"
    _note(_store(proposal_vault), path="subtree-other/new.md", proposal=first.proposal)
    _note(_store(proposal_vault), path="outside.md", proposal=first.proposal)
    listed = store.list_proposals()
    assert listed.model_dump() == {
        "proposals": [{"proposal": first.proposal, "paths": ["note.md", "計画/new.md"]}]
    }
    read = store.read(ReadRequest(path="note.md", proposal=first.proposal))
    assert read.numbered_content == "1\tchanged\n"
    assert read.path == "note.md"
    git(remote, "update-ref", "-d", f"refs/heads/{first.proposal}")
    assert store.list_proposals().proposals == []


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("../note.md", DomainErrorCode.INVALID_PATH),
        (".hidden/secret.md", DomainErrorCode.ACCESS_DENIED),
        ("link.md", DomainErrorCode.ACCESS_DENIED),
        ("linked/note.md", DomainErrorCode.ACCESS_DENIED),
        ("plain.txt", DomainErrorCode.UNSUPPORTED_TYPE),
        ("note.md/new.md", DomainErrorCode.NOT_A_DIRECTORY),
        ("missing.md", DomainErrorCode.NOT_FOUND),
        ("subtree.md", DomainErrorCode.NOT_A_FILE),
    ],
)
def test_branch_path_policy(
    proposal_vault: ProposalVault, path: str, code: DomainErrorCode | None
) -> None:
    """Commit contents follow the root's policy, including symlink parents."""
    checkout, _, _, git = proposal_vault
    (checkout / "subtree.md").mkdir()
    (checkout / "subtree.md" / "child.md").write_text("child")
    git(checkout, "add", ".")
    git(checkout, "commit", "-m", "Directory")
    store = _store(proposal_vault)
    branch = _note(store).proposal
    for call in (
        lambda: store.read(ReadRequest(path=path, proposal=branch)),
        lambda: _edit(store, path=path, proposal=branch),
    ):
        with pytest.raises(KnowledgeError) as error:
            call()
        assert error.value.code == code
    if code not in {DomainErrorCode.NOT_FOUND, DomainErrorCode.NOT_A_FILE}:
        with pytest.raises(KnowledgeError) as error:
            _note(store, path=path, proposal=branch)
        assert error.value.code == code


def test_missing_branch_and_safe_git_failure(proposal_vault: ProposalVault) -> None:
    """A missing branch differs from a failed operation, with safe messages."""
    _, state, _, _ = proposal_vault
    store = _store(proposal_vault)
    for call in (
        lambda: _note(store, proposal="proposal/missing-ab12"),
        lambda: store.read(
            ReadRequest(path="note.md", proposal="proposal/missing-ab12")
        ),
    ):
        with pytest.raises(KnowledgeError) as error:
            call()
        assert error.value.code == DomainErrorCode.NOT_FOUND
    store.vault_remote = str(state / "missing-private.git")
    with pytest.raises(KnowledgeError) as error:
        _note(store)
    assert error.value.code == DomainErrorCode.GIT_FAILED
    assert str(state) not in error.value.message


def test_branch_collision_does_not_overwrite(
    proposal_vault: ProposalVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a repeated random suffix cannot overwrite an existing branch."""
    monkeypatch.setattr(
        "knowledge_server.core.writer.secrets.token_hex", lambda _: "abcd"
    )
    store = _store(proposal_vault)
    first = _note(store, content="first")
    with pytest.raises(KnowledgeError) as error:
        _note(store, content="second")
    assert error.value.code == DomainErrorCode.ALREADY_EXISTS
    assert (
        store.read(ReadRequest(path="new.md", proposal=first.proposal)).content_sha256
        == first.content_sha256
    )


def test_revision_lease_refuses_remote_race(
    proposal_vault: ProposalVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A remote writer changing the fetched tip defeats the revision's lease."""
    _, _, remote, git = proposal_vault
    store = _store(proposal_vault)
    first = _note(store)
    original = store.commit_and_push
    main = git(remote, "rev-parse", "main").decode().strip()

    def raced(*args, **kwargs):
        git(remote, "update-ref", f"refs/heads/{first.proposal}", main)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "commit_and_push", raced)
    with pytest.raises(KnowledgeError) as error:
        _edit(store, proposal=first.proposal)
    assert error.value.code == DomainErrorCode.STALE_CONTENT
    assert git(remote, "rev-parse", first.proposal).decode().strip() == main


@pytest.mark.parametrize(
    ("raw", "edits", "expected", "code"),
    [
        (
            b"\xef\xbb\xbfone\r\ntwo\r\n",
            [TextEdit(old="one\ntwo", new="a\nb")],
            b"\xef\xbb\xbfa\r\nb\r\n",
            None,
        ),
        (b"a\rb\r\r\nc\r\n", [TextEdit(old="c", new="d")], b"a\rb\r\r\nd\r\n", None),
        (b"one\ntwo", [TextEdit(old="two", new="three")], b"one\nthree", None),
        (b"one\r\ntwo\n", None, None, DomainErrorCode.MIXED_LINE_ENDINGS),
        (
            b"aaa\n",
            [TextEdit(old="aa", new="b")],
            None,
            DomainErrorCode.TEXT_NOT_UNIQUE,
        ),
        (
            b"one\ntwo\n",
            [TextEdit(old="absent", new="b")],
            None,
            DomainErrorCode.TEXT_NOT_FOUND,
        ),
        (
            b"one\ntwo\n",
            [TextEdit(old="one", new="two"), TextEdit(old="two", new="b")],
            None,
            DomainErrorCode.TEXT_NOT_UNIQUE,
        ),
        (b"a\x00b", None, None, DomainErrorCode.INVALID_ENCODING),
        (b"\xff", None, None, DomainErrorCode.INVALID_ENCODING),
    ],
)
def test_edit_semantics(
    proposal_vault: ProposalVault,
    raw: bytes,
    edits: list[TextEdit] | None,
    expected: bytes | None,
    code: DomainErrorCode | None,
) -> None:
    """Commit edits preserve the exact-replacement and encoding rules."""
    checkout, _, remote, git = proposal_vault
    (checkout / "note.md").write_bytes(raw)
    git(checkout, "add", "note.md")
    git(checkout, "commit", "--allow-empty", "-m", "Encoding fixture")
    store = _store(proposal_vault)
    if code:
        with pytest.raises(KnowledgeError) as error:
            _edit(store, raw=raw, edits=edits)
        assert error.value.code == code
        assert not store.list_proposals().proposals
    else:
        result = _edit(store, raw=raw, edits=edits)
        assert expected is not None
        assert git(remote, "show", f"{result.proposal}:note.md") == expected
        read = store.read(ReadRequest(path="note.md", proposal=result.proposal))
        assert read.content_sha256 == _sha(expected)
        assert "\ufeff" not in read.numbered_content


def test_stale_hash_and_injected_limits(proposal_vault: ProposalVault) -> None:
    """Stale hashes and request, result, listing, and read limits are enforced."""
    store = _store(proposal_vault)
    with pytest.raises(KnowledgeError) as error:
        _edit(store, raw=b"stale")
    assert error.value.code == DomainErrorCode.STALE_CONTENT
    small = _store(proposal_vault, limits=replace(DEFAULT_LIMITS, max_file_bytes=4))
    for call in (lambda: _note(small, content="12345"), lambda: _edit(small)):
        with pytest.raises(KnowledgeError) as error:
            call()
        assert error.value.code == DomainErrorCode.FILE_TOO_LARGE
    text = _store(
        proposal_vault, limits=replace(DEFAULT_LIMITS, max_write_text_bytes=2)
    )
    with pytest.raises(KnowledgeError) as error:
        _note(text)
    assert error.value.code == DomainErrorCode.INVALID_ARGUMENT
    branch = _note(store, content="long line\n").proposal
    with pytest.raises(KnowledgeError) as error:
        small.read(ReadRequest(path="new.md", proposal=branch))
    assert error.value.code == DomainErrorCode.FILE_TOO_LARGE
    limited = _store(
        proposal_vault, limits=replace(DEFAULT_LIMITS, max_proposal_entries=1)
    )
    with pytest.raises(KnowledgeError) as error:
        limited.list_proposals()
    assert error.value.code == DomainErrorCode.DIRECTORY_LIMIT_EXCEEDED


def test_listing_filters_paths_and_uses_merge_base(
    proposal_vault: ProposalVault,
) -> None:
    """Listing excludes invisible entries and includes deleted visible notes."""
    checkout, _, _, git = proposal_vault
    store = _store(proposal_vault)
    branch = _note(store).proposal
    git(checkout, "fetch", "origin", branch)
    git(checkout, "checkout", "-b", branch, "FETCH_HEAD")
    (checkout / "note.md").unlink()
    (checkout / "subtree" / "note.md").unlink()
    (checkout / "subtree").rmdir()
    (checkout / "subtree").symlink_to("new.md")
    (checkout / ".hidden" / "secret.md").write_text("changed")
    (checkout / "plain.txt").write_text("changed")
    (checkout / "new-link.md").symlink_to("new.md")
    git(checkout, "add", ".")
    git(checkout, "commit", "-m", "More changes")
    git(checkout, "push", "origin", branch)
    git(checkout, "checkout", "main")
    (checkout / "subtree" / "later.md").write_text("main advanced")
    git(checkout, "add", ".")
    git(checkout, "commit", "-m", "Advance main")
    git(checkout, "push", "origin", "main")
    git(checkout, "push", "origin", "main:refs/heads/proposal/日本語-ab12")
    assert store.list_proposals().proposals[0].paths == [
        "new.md",
        "note.md",
        "subtree/note.md",
    ]


@pytest.mark.parametrize(
    "selection",
    [
        {},
        {"topic": "Upper"},
        {"topic": "test\n"},
        {"topic": "-bad"},
        {"topic": "a--b"},
        {"topic": "a" * 41},
        {"proposal": "main"},
        {"proposal": "proposal/topic-ABCD"},
        {"proposal": "proposal/topic-abcd\n"},
        {"topic": "a", "proposal": "proposal/a-abcd"},
    ],
)
def test_selection_validation(selection: dict[str, str]) -> None:
    """Unsafe branch arguments and ambiguous selections fail before Git."""
    with pytest.raises(ValidationError):
        ProposeNoteRequest(
            path="new.md", content="text", summary="Add a note", **selection
        )


@pytest.mark.parametrize("text", ["a\x00b", "a\rb", "\ufeffa", "a\ufeffb"])
def test_write_text_validation(text: str) -> None:
    """Text validation does not echo request values."""
    with pytest.raises(ValidationError) as error:
        ProposeNoteRequest(
            path="new.md", content=text, summary="Add a note", topic="test"
        )
    message = invalid_argument_message(ProposeNoteRequest, error.value)
    assert "NUL, CR, or U+FEFF" in message
    assert text not in message


@pytest.mark.parametrize("case", ["output", "local-timeout", "network-timeout"])
def test_git_runner_bounds_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """The Git runner bounds both streams and reaps a stopped process."""
    import os
    import sys

    from knowledge_server.core import git as git_module

    pid_file = tmp_path / "pid"
    executable = tmp_path / "git"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, time\n"
        f'open({str(pid_file)!r}, "w").write(str(os.getpid()))\n'
        'os.write(1, b"a" * 60)\n'
        'os.write(2, b"b" * 60)\n'
        "time.sleep(60)\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    if case == "output":
        monkeypatch.setattr(git_module, "MAX_OUTPUT_BYTES", 100)
    else:
        name = (
            "NETWORK_DEADLINE_SECONDS"
            if case == "network-timeout"
            else "LOCAL_DEADLINE_SECONDS"
        )
        monkeypatch.setattr(git_module, name, 0.2)
    with pytest.raises(KnowledgeError) as error:
        git_module.run_git(tmp_path, [], network=case == "network-timeout")
    assert error.value.code == DomainErrorCode.GIT_FAILED
    if pid_file.exists():
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)


def test_git_environment_isolation_and_subtree_safe_directory(
    proposal_vault: ProposalVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git gets fixed identity and only the checkout top-level ownership exception."""
    from knowledge_server.core import git as git_module

    checkout, state, remote, _ = proposal_vault
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "user.name")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "untrusted")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "untrusted")
    original = git_module.subprocess.Popen
    calls = []

    def recording(command, **kwargs):
        calls.append((command, kwargs["env"]))
        return original(command, **kwargs)

    monkeypatch.setattr(git_module.subprocess, "Popen", recording)
    store = ProposalBranches(
        PathPolicy(checkout / "subtree"), str(remote), state_directory=state
    )
    result = _note(store)
    store.list_proposals()
    store.read(ReadRequest(path="new.md", proposal=result.proposal))
    assert calls
    for command, environment in calls:
        assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
        assert environment["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert environment["GIT_TERMINAL_PROMPT"] == "0"
        assert environment["GIT_AUTHOR_NAME"] == "knowledge-server"
        assert environment["GIT_COMMITTER_NAME"] == "knowledge-server"
        assert "GIT_CONFIG_COUNT" not in environment
        if str(remote) in command:
            assert environment["GIT_SSH_COMMAND"] == (
                "ssh -i /etc/knowledge-server/vault-remote-key -o IdentitiesOnly=yes "
                "-o BatchMode=yes -o UserKnownHostsFile=/etc/knowledge-server/known_hosts"
            )
            assert "origin" not in command
            if "fetch" in command:
                assert "--prune" in command
                assert any(":refs/vault/" in argument for argument in command)
        else:
            assert "GIT_SSH_COMMAND" not in environment
        if "-c" in command:
            assert command[command.index("-c") + 1] == f"safe.directory={checkout}"


def test_invalid_proposal_does_not_run_git(
    proposal_vault: ProposalVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Core checks the branch namespace even when model validation is bypassed."""
    from knowledge_server.core import git as git_module

    store = _store(proposal_vault)

    def unexpected(*args, **kwargs):
        raise AssertionError("Invalid branch reached Git")

    monkeypatch.setattr(git_module, "run_git", unexpected)
    with pytest.raises(KnowledgeError) as error:
        store.base("main")
    assert error.value.code == DomainErrorCode.INVALID_ARGUMENT


def test_internal_state_lifecycle(proposal_vault: ProposalVault) -> None:
    """Each start imports HEAD and can recreate deleted server state."""
    import shutil

    checkout, state, _, git = proposal_vault
    for attempt in range(3):
        if attempt == 2:
            shutil.rmtree(state)
        store = _store(proposal_vault)
        expected = git(checkout, "rev-parse", "HEAD").strip()
        assert store.command("rev-parse", "refs/knowledge/checkout").strip() == expected
        assert store.command("rev-parse", "--is-bare-repository").strip() == b"true"
        assert store.command("remote") == b""
        assert b"sshcommand" not in store.command("config", "--local", "--list").lower()
        assert not (store.repository / "objects" / "info" / "alternates").exists()
        result = _note(store, path=f"new-{attempt}.md")
        assert (
            git(store.repository, "rev-parse", "refs/knowledge/checkout").strip()
            == expected
        )
        assert result.proposal
        (checkout / "note.md").write_text(f"start {attempt}\n")
        git(checkout, "add", ".")
        git(checkout, "commit", "-m", "Advance checkout")


def test_imported_objects_are_independent(proposal_vault: ProposalVault) -> None:
    """Imported history and blobs remain readable without checkout objects."""
    import shutil

    checkout, _, _, _ = proposal_vault
    store = _store(proposal_vault)
    base = store.command("rev-parse", "refs/knowledge/checkout").decode().strip()
    shutil.rmtree(checkout / ".git" / "objects")
    assert store.load(base, "note.md")[0] == b"one\ntwo\n"
    assert store.command("log", "--format=%s", base) == b"Invented notes\n"


def test_new_proposal_uses_imported_oid(
    proposal_vault: ProposalVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout move after import cannot change the new proposal's base."""
    from knowledge_server.core import git as git_module

    checkout, _, remote, git = proposal_vault
    store = _store(proposal_vault)
    original = git_module.run_git
    imports = []

    def raced(directory, arguments, **kwargs):
        result = original(directory, arguments, **kwargs)
        if "fetch" in arguments and str(checkout) in arguments:
            assert kwargs["network"] is True
            assert arguments[arguments.index("-c") + 1] == f"safe.directory={checkout}"
            assert git_module.PROPOSAL_LOCK.locked()
            imports.append(git(checkout, "rev-parse", "HEAD").strip())
            (checkout / "note.md").write_text("cron moved HEAD\n")
            git(checkout, "add", ".")
            git(checkout, "commit", "-m", "Cron move")
        return result

    monkeypatch.setattr(git_module, "run_git", raced)
    # The write must refresh the startup import first.
    (checkout / "other.md").write_text("new committed note\n")
    git(checkout, "add", ".")
    git(checkout, "commit", "-m", "After startup")
    result = _edit(store)
    assert len(imports) == 1
    assert git(remote, "rev-parse", f"{result.proposal}^").strip() == imports[0]
    assert git(remote, "show", f"{result.proposal}:note.md") == b"three\ntwo\n"
    assert git(remote, "show", f"{result.proposal}:other.md") == b"new committed note\n"
