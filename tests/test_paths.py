"""Tests for the common API-path and immediate-discovery policy."""

import os
from pathlib import Path
from typing import Never

import pytest

from knowledge_server.core.models import DomainErrorCode, KnowledgeError
from knowledge_server.core.paths import PathPolicy, TargetKind


def _code(error: pytest.ExceptionInfo[KnowledgeError]) -> DomainErrorCode:
    return error.value.code


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Build a synthetic vault with visible, hidden, and ignored notes."""
    (tmp_path / "Notes").mkdir()
    (tmp_path / "Notes" / "café note.md").write_text("text", encoding="utf-8")
    (tmp_path / "Notes" / "asset.txt").write_text("text", encoding="utf-8")
    (tmp_path / "Notes" / "UPPER.MD").write_text("text", encoding="utf-8")
    (tmp_path / "Notes" / "folder.md").mkdir()
    (tmp_path / ".hidden.md").write_text("private", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
    (tmp_path / "ignored.md").write_text("still visible", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    return tmp_path


def test_relative_unicode_spaces(vault: Path) -> None:
    """The policy preserves API spelling."""
    policy = PathPolicy(vault)
    assert (
        policy.resolve("Notes/café note.md", TargetKind.FILE).relative_path
        == "Notes/café note.md"
    )


@pytest.mark.parametrize(
    ("path", "target", "relative_path", "code"),
    [
        ("", TargetKind.DIRECTORY, "", None),
        ("", TargetKind.EITHER, "", None),
        ("", TargetKind.FILE, None, DomainErrorCode.INVALID_PATH),
        ("Notes/", TargetKind.DIRECTORY, "Notes", None),
        ("Notes/", TargetKind.EITHER, "Notes", None),
        ("Notes//", TargetKind.DIRECTORY, None, DomainErrorCode.INVALID_PATH),
        ("Notes/café note.md/", TargetKind.FILE, None, DomainErrorCode.INVALID_PATH),
        (
            "Notes/café note.md/",
            TargetKind.DIRECTORY,
            None,
            DomainErrorCode.NOT_A_DIRECTORY,
        ),
    ],
)
def test_directory_normalization(
    vault: Path,
    path: str,
    target: TargetKind,
    relative_path: str | None,
    code: DomainErrorCode | None,
) -> None:
    """Empty and trailing-slash forms have one consistent target policy."""
    policy = PathPolicy(vault)
    if code is None:
        assert policy.resolve(path, target).relative_path == relative_path
    else:
        with pytest.raises(KnowledgeError) as error:
            policy.resolve(path, target)
        assert _code(error) is code


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "C:/Windows",
        "C:relative",
        r"\\server\share",
        r"Notes\\a.md",
        "Notes//a.md",
        "Notes/./a.md",
        "Notes/../a.md",
        "Notes/\x00a.md",
        "Notes/\x01a.md",
        "Notes/\x85a.md",
        "Notes/a.md//",
    ],
)
def test_malformed_paths_are_rejected_before_lookup(vault: Path, path: str) -> None:
    """Lexical forms cannot reveal whether an outside target exists."""
    with pytest.raises(KnowledgeError) as error:
        PathPolicy(vault).resolve(path, TargetKind.EITHER)
    assert _code(error) is DomainErrorCode.INVALID_PATH
    assert "private" not in str(error.value)
    assert str(vault) not in str(error.value)


@pytest.mark.parametrize(
    "path",
    [
        ".hidden.md",
        "linked.md",
        "inroot-link.md",
        "broken.md",
        "Notes/linked-dir/outside-sentinel.md",
        "Notes/hidden-link.md",
        "pipe.md",
    ],
)
def test_hidden_symlink_special_and_root_prefix_collision_are_denied(
    vault: Path, tmp_path: Path, path: str
) -> None:
    """Component-wise lstat prevents link following and prefix containment bugs."""
    outside = tmp_path.parent / "outside-sentinel.md"
    outside.write_text("outside secret", encoding="utf-8")
    (vault / "linked.md").symlink_to(outside)
    (vault / "inroot-link.md").symlink_to(vault / "ignored.md")
    (vault / "broken.md").symlink_to(tmp_path / "does-not-exist")
    (vault / "Notes" / "linked-dir").symlink_to(
        tmp_path.parent, target_is_directory=True
    )
    (vault / "Notes" / "hidden-link.md").symlink_to(vault / ".hidden.md")
    fifo = vault / "pipe.md"
    os.mkfifo(fifo)
    policy = PathPolicy(vault)
    with pytest.raises(KnowledgeError) as error:
        policy.resolve(path, TargetKind.FILE)
    assert _code(error) is DomainErrorCode.ACCESS_DENIED
    assert "outside secret" not in str(error.value)
    assert str(outside) not in str(error.value)
    discovered = {entry.relative_path for entry in policy.discover_immediate("")}
    assert {"linked.md", "inroot-link.md", "broken.md", "pipe.md"}.isdisjoint(
        discovered
    )
    nested = {
        entry.relative_path: entry.kind for entry in policy.discover_immediate("Notes")
    }
    assert nested == {
        "Notes/UPPER.MD": TargetKind.FILE,
        "Notes/café note.md": TargetKind.FILE,
        "Notes/folder.md": TargetKind.DIRECTORY,
    }


def test_policy_checks_all_components_before_any_existence_probe(vault: Path) -> None:
    """A missing ancestor cannot reveal later hidden names or file suffix behavior."""
    policy = PathPolicy(vault)
    with pytest.raises(KnowledgeError) as hidden:
        policy.resolve("missing/.hidden.md", TargetKind.FILE)
    assert _code(hidden) is DomainErrorCode.ACCESS_DENIED
    with pytest.raises(KnowledgeError) as suffix:
        policy.resolve("missing/file.txt", TargetKind.FILE)
    assert _code(suffix) is DomainErrorCode.UNSUPPORTED_TYPE


@pytest.mark.parametrize(
    ("path", "target", "code"),
    [
        ("missing.txt", TargetKind.FILE, DomainErrorCode.UNSUPPORTED_TYPE),
        ("Notes/asset.txt", TargetKind.EITHER, DomainErrorCode.UNSUPPORTED_TYPE),
        ("Notes/asset.txt", TargetKind.DIRECTORY, DomainErrorCode.NOT_A_DIRECTORY),
        ("Notes", TargetKind.FILE, DomainErrorCode.UNSUPPORTED_TYPE),
        ("Notes/café note.md", TargetKind.DIRECTORY, DomainErrorCode.NOT_A_DIRECTORY),
        ("Notes/folder.md", TargetKind.FILE, DomainErrorCode.NOT_A_FILE),
        ("missing.md", TargetKind.FILE, DomainErrorCode.NOT_FOUND),
    ],
)
def test_extension_type_and_existence_mappings(
    vault: Path, path: str, target: TargetKind, code: DomainErrorCode
) -> None:
    """Suffix, wrong target kind, and missing target mappings are stable."""
    policy = PathPolicy(vault)
    with pytest.raises(KnowledgeError) as error:
        policy.resolve(path, target)
    assert _code(error) is code


def test_paths_are_not_url_decoded_and_case_insensitive_suffixes_are_eligible(
    vault: Path,
) -> None:
    """API paths retain their literal Unicode spelling and accepted suffix case."""
    policy = PathPolicy(vault)
    assert policy.resolve("Notes/UPPER.MD", TargetKind.FILE).kind is TargetKind.FILE
    with pytest.raises(KnowledgeError) as encoded:
        policy.resolve("Notes/caf%C3%A9%20note.md", TargetKind.FILE)
    assert _code(encoded) is DomainErrorCode.NOT_FOUND


def test_unicode_colon_names_are_not_windows_drive_paths(vault: Path) -> None:
    """Only ASCII Windows drive prefixes are malformed API paths."""
    (vault / "é:note.md").write_text("text", encoding="utf-8")
    (vault / "日:note.md").write_text("text", encoding="utf-8")
    policy = PathPolicy(vault)
    assert policy.resolve("é:note.md", TargetKind.FILE).relative_path == "é:note.md"
    assert policy.resolve("日:note.md", TargetKind.FILE).relative_path == "日:note.md"
    entries = {entry.relative_path for entry in policy.discover_immediate("")}
    assert {"é:note.md", "日:note.md"} <= entries


def test_immediate_discovery_matches_direct_visibility_and_is_bounded(
    vault: Path,
) -> None:
    """Ignored Markdown and empty ordinary directories are visible after bounded scans."""
    policy = PathPolicy(vault, immediate_entry_limit=4)
    with pytest.raises(KnowledgeError) as error:
        tuple(policy.discover_immediate(""))
    assert _code(error) is DomainErrorCode.DIRECTORY_LIMIT_EXCEEDED

    policy = PathPolicy(vault, immediate_entry_limit=20)
    entries = {
        entry.relative_path: entry.kind for entry in policy.discover_immediate("")
    }
    assert entries == {"Notes": "directory", "empty": "directory", "ignored.md": "file"}
    assert policy.resolve("ignored.md", TargetKind.FILE).relative_path in entries


def test_discovery_does_not_expose_names_rejected_by_direct_access(vault: Path) -> None:
    """Discovery and direct resolution use identical lexical visibility rules."""
    (vault / "literal\\name.md").write_text("text", encoding="utf-8")
    (vault / "C:drive.md").write_text("text", encoding="utf-8")
    (vault / "control\x85name.md").write_text("text", encoding="utf-8")
    entries = {
        entry.relative_path for entry in PathPolicy(vault).discover_immediate("")
    }
    assert "literal\\name.md" not in entries
    assert "C:drive.md" not in entries
    assert "control\x85name.md" not in entries


def test_posix_colon_name_below_a_directory_remains_eligible(vault: Path) -> None:
    """Only an initial Windows-drive form is rejected, not ordinary POSIX names."""
    (vault / "Notes" / "C:note.md").write_text("text", encoding="utf-8")
    policy = PathPolicy(vault)
    assert (
        policy.resolve("Notes/C:note.md", TargetKind.FILE).relative_path
        == "Notes/C:note.md"
    )


def test_containment_check_rejects_prefix_sibling_and_preserves_missing_mapping(
    vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolved ancestry uses path components and retains concurrent missing errors."""
    sibling = tmp_path.parent / f"{tmp_path.name}-other"
    sibling.mkdir()
    sentinel = sibling / "café note.md"
    sentinel.write_text("outside secret", encoding="utf-8")
    policy = PathPolicy(vault)
    original_resolve = Path.resolve

    def prefix_sibling(path: Path, *, strict: bool = False) -> Path:
        if path == vault / "Notes" / "café note.md":
            return sentinel
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", prefix_sibling)
    with pytest.raises(KnowledgeError) as denied:
        policy.resolve("Notes/café note.md", TargetKind.FILE)
    assert _code(denied) is DomainErrorCode.ACCESS_DENIED
    assert "outside secret" not in str(denied.value)
    assert str(sibling) not in str(denied.value)

    def disappeared(path: Path, *, strict: bool = False) -> Path:
        if path == vault / "Notes" / "café note.md":
            raise FileNotFoundError
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", disappeared)
    with pytest.raises(KnowledgeError) as missing:
        policy.resolve("Notes/café note.md", TargetKind.FILE)
    assert _code(missing) is DomainErrorCode.NOT_FOUND


def test_lstat_and_scandir_failures_are_safe_access_errors(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Injected permission failures cannot expose OS errors or vault paths."""
    original_lstat = Path.lstat

    def deny_file(path: Path) -> os.stat_result:
        if path.name == "café note.md":
            raise PermissionError("private /host/path")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", deny_file)
    with pytest.raises(KnowledgeError) as stat_error:
        PathPolicy(vault).resolve("Notes/café note.md", TargetKind.FILE)
    assert _code(stat_error) is DomainErrorCode.ACCESS_DENIED
    assert "private" not in str(stat_error.value)

    def deny_scan(*_: object, **__: object) -> Never:
        raise PermissionError("private /host/path")

    monkeypatch.setattr("knowledge_server.core.paths.os.scandir", deny_scan)
    with pytest.raises(KnowledgeError) as scan_error:
        PathPolicy(vault).discover_immediate("Notes")
    assert _code(scan_error) is DomainErrorCode.ACCESS_DENIED
    assert "private" not in str(scan_error.value)


def test_missing_directory_during_scan_is_not_found(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory removed after validation has the normal missing-target mapping."""

    def missing_scan(*_: object, **__: object) -> Never:
        raise FileNotFoundError

    monkeypatch.setattr("knowledge_server.core.paths.os.scandir", missing_scan)
    with pytest.raises(KnowledgeError) as error:
        PathPolicy(vault).discover_immediate("Notes")
    assert _code(error) is DomainErrorCode.NOT_FOUND
