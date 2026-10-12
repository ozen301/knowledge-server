"""Shared pytest configuration."""

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clear_knowledge_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove inherited vault and state-directory settings before each test.

    Tests must create invented vaults and use temporary state directories. This
    guards environment lookups only; it does not isolate the filesystem.
    """
    monkeypatch.delenv("KNOWLEDGE_ROOT", raising=False)
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)


type ProposalVault = tuple[Path, Path, Path, Callable[..., bytes]]


@pytest.fixture
def proposal_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ProposalVault:
    """Create an invented checkout, local remote, and isolated state directory."""
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.invalid",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.invalid",
    )

    def git(directory: Path, *arguments: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(directory), *arguments],
            env=environment,
            capture_output=True,
            check=True,
            timeout=10,
        ).stdout

    remote = tmp_path / "remote.git"
    checkout = tmp_path / "checkout"
    state = tmp_path / "state"
    monkeypatch.setenv("STATE_DIRECTORY", str(state))
    git(tmp_path, "init", "--bare", "--initial-branch=main", str(remote))
    git(tmp_path, "clone", str(remote), str(checkout))
    (checkout / "note.md").write_text("one\ntwo\n")
    (checkout / "subtree").mkdir()
    (checkout / "subtree" / "note.md").write_text("subtree\n")
    (checkout / ".hidden").mkdir()
    (checkout / ".hidden" / "secret.md").write_text("invented secret")
    (checkout / "link.md").symlink_to("note.md")
    (checkout / "linked").symlink_to("subtree", target_is_directory=True)
    (checkout / "plain.txt").write_text("text")
    git(checkout, "add", ".")
    git(checkout, "commit", "-m", "Invented notes")
    git(checkout, "push", "origin", "main")
    return checkout, state, remote, git
