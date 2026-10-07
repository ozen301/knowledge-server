"""End-to-end tests for `deploy/review-proposals` with invented notes.

Each test builds a bare remote, a local vault checkout with an inbox, and a review
clone in a temporary directory. Stand-ins for `sudo` and `systemctl` come
first on `PATH`; the `systemctl` stand-in records its arguments.
"""

import hashlib
import os
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "deploy" / "review-proposals"

FAKE_SUDO = """#!/bin/sh
# Drop the options that the script passes, then run the command directly.
# `rm` fails while the file named by SUDO_FAIL_RM exists.
while [ $# -gt 0 ]; do
    case $1 in
        -u) shift 2 ;;
        -*) shift ;;
        *) break ;;
    esac
done
if [ "$1" = rm ] && [ -e "$SUDO_FAIL_RM" ]; then
    exit 1
fi
exec "$@"
"""

FAKE_SYSTEMCTL = """#!/bin/sh
# Record the call; fail while the file named by SYSTEMCTL_FAIL_<ACTION> exists.
echo "$*" >> "$SYSTEMCTL_LOG"
case $1 in
    stop) [ ! -e "$SYSTEMCTL_FAIL_STOP" ] ;;
    start) [ ! -e "$SYSTEMCTL_FAIL_START" ] ;;
esac
"""


@dataclass
class Setup:
    """The repositories and environment of one test.

    Attributes:
        tmp: The temporary directory that holds everything.
        remote: The bare vault remote.
        served: The local vault checkout; the root unless a test uses a subtree.
        review: The review clone.
        env: The environment for the script and for Git commands.
        systemctl_log: The file in which the `systemctl` stand-in records calls.
    """

    tmp: Path
    remote: Path
    served: Path
    review: Path
    env: dict[str, str]
    systemctl_log: Path

    @property
    def inbox(self) -> Path:
        """Return the inbox of the root."""
        return Path(self.env["KNOWLEDGE_ROOT"]) / "inbox"

    def git(self, cwd: Path, *args: str) -> str:
        """Run Git in `cwd`, fail on a nonzero status, and return stdout."""
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=self.env,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout

    def run(self, *args: str, answer: str = "") -> subprocess.CompletedProcess[str]:
        """Run the review script with `answer` on stdin."""
        return subprocess.run(
            [str(SCRIPT), *args],
            env=self.env,
            input=answer,
            capture_output=True,
            text=True,
            check=False,
        )

    def calls(self) -> list[str]:
        """Return the recorded `systemctl` calls."""
        if not self.systemctl_log.exists():
            return []
        return self.systemctl_log.read_text().splitlines()

    def state_file(self) -> Path:
        """Return the path of the state file in the review clone."""
        return self.review / ".git" / "review-proposals"

    def propose(self, path: str, text: str, base: str | None = None) -> None:
        """Save a proposal as the write tools do, with an optional base copy."""
        proposal = self.inbox / path
        proposal.parent.mkdir(parents=True, exist_ok=True)
        proposal.write_text(text)
        if base is not None:
            base_copy = self.inbox / ".base" / path
            base_copy.parent.mkdir(parents=True, exist_ok=True)
            base_copy.write_text(base)

    def commit_and_push(self, *paths: str, message: str = "review") -> None:
        """Commit `paths` in the review clone and push them."""
        self.git(self.review, "add", "--", *paths)
        self.git(self.review, "commit", "--quiet", "-m", message)
        self.git(self.review, "push", "--quiet")

    def change_remote(self, path: str, text: str | None) -> None:
        """Commit a change of `path` to the remote from another clone."""
        other = self.tmp / "other"
        if not other.exists():
            self.git(self.tmp, "clone", "--quiet", str(self.remote), str(other))
        self.git(other, "pull", "--quiet", "--ff-only")
        target = other / path
        if text is None:
            self.git(other, "rm", "--quiet", "--", path)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
            self.git(other, "add", "--", path)
        self.git(other, "commit", "--quiet", "-m", "remote change")
        self.git(other, "push", "--quiet")


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


NOTES = {
    "Plan.md": "# Plan\n\nfirst\nsecond\nthird\n",
    "Projects/Garden.md": "# Garden\n\ntomatoes\n",
}


def _make_setup(tmp_path: Path, notes: dict[str, str], subtree: str = "") -> Setup:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "sudo", FAKE_SUDO)
    _write_executable(bin_dir / "systemctl", FAKE_SYSTEMCTL)
    git_config = tmp_path / "gitconfig"
    git_config.write_text("[user]\n\tname = Test\n\temail = test@example.com\n")
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "GIT_CONFIG_GLOBAL": str(git_config),
        "GIT_CONFIG_NOSYSTEM": "1",
        "SYSTEMCTL_LOG": str(tmp_path / "systemctl.log"),
        "SYSTEMCTL_FAIL_STOP": str(tmp_path / "fail-stop"),
        "SYSTEMCTL_FAIL_START": str(tmp_path / "fail-start"),
        "SUDO_FAIL_RM": str(tmp_path / "fail-rm"),
    }
    remote = tmp_path / "remote.git"
    served = tmp_path / "served"
    review = tmp_path / "review"
    setup = Setup(tmp_path, remote, served, review, env, tmp_path / "systemctl.log")
    setup.git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    setup.git(tmp_path, "init", "--quiet", "-b", "main", str(seed))
    for path, text in notes.items():
        note = seed / subtree / path
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(text)
    setup.git(seed, "add", "--all")
    setup.git(seed, "commit", "--quiet", "-m", "notes")
    setup.git(seed, "push", "--quiet", str(remote), "main")
    setup.git(tmp_path, "clone", "--quiet", str(remote), str(served))
    setup.git(tmp_path, "clone", "--quiet", str(remote), str(review))
    root = served / subtree if subtree else served
    (root / "inbox").mkdir()
    with (served / ".git" / "info" / "exclude").open("a") as exclude:
        exclude.write(f"/{subtree}{'/' if subtree else ''}inbox/\n")
    env["KNOWLEDGE_ROOT"] = str(root)
    env["KNOWLEDGE_REVIEW_CLONE"] = str(review)
    return setup


@pytest.fixture
def setup(tmp_path: Path) -> Setup:
    """Return a setup with two committed notes and an empty inbox."""
    return _make_setup(tmp_path, NOTES)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _status(setup: Setup) -> str:
    return setup.git(setup.review, "status", "--porcelain", "--untracked-files=all")


def test_empty_inbox_changes_nothing(setup: Setup) -> None:
    """With no proposals, the script exits without a state file or a stop."""
    result = setup.run()
    assert result.returncode == 0
    assert result.stdout.strip() == "No proposals."
    assert not setup.state_file().exists()
    assert setup.calls() == []


def test_hidden_files_are_not_proposals(setup: Setup) -> None:
    """Base copies and hidden temporary files are never applied."""
    (setup.inbox / ".base").mkdir()
    (setup.inbox / ".base" / "Plan.md").write_text("x\n")
    (setup.inbox / ".tmp-note.md").write_text("x\n")
    result = setup.run()
    assert result.returncode == 0
    assert result.stdout.strip() == "No proposals."


def test_unknown_argument(setup: Setup) -> None:
    """An unknown argument is a usage error."""
    result = setup.run("--bogus")
    assert result.returncode == 2
    assert "usage" in result.stderr


def test_done_without_review(setup: Setup) -> None:
    """`--done` without a review in progress does nothing."""
    result = setup.run("--done")
    assert result.returncode == 0
    assert result.stdout.strip() == "No review in progress."
    assert setup.calls() == []


def test_accept_edit_and_new_notes(setup: Setup) -> None:
    """A clean edit and new notes go through the whole review.

    The inbox itself stays after its last proposal is deleted.
    """
    plan = NOTES["Plan.md"]
    setup.propose("Plan.md", plan.replace("second", "SECOND"), base=plan)
    setup.propose("Ideas/New.md", "# New\n")
    setup.propose("-dash.md", "# Dash\n")

    result = setup.run()
    assert result.returncode == 0, result.stderr
    assert "applied" in result.stdout
    assert "review-proposals --done" in result.stdout
    assert (setup.review / "Plan.md").read_text() == plan.replace("second", "SECOND")
    diff_names = setup.git(setup.review, "diff", "--name-only").splitlines()
    assert sorted(diff_names) == ["-dash.md", "Ideas/New.md", "Plan.md"]
    assert setup.calls() == []

    setup.commit_and_push("Plan.md", "Ideas/New.md", "-dash.md")
    result = setup.run("--done")
    assert result.returncode == 0, result.stderr
    assert setup.calls() == ["stop knowledge-server", "start knowledge-server"]
    assert setup.inbox.is_dir()
    assert list(setup.inbox.iterdir()) == []
    assert not setup.state_file().exists()


def test_edit_merges_with_later_vault_change(setup: Setup) -> None:
    """A change that reached the note after the proposal is kept."""
    plan = NOTES["Plan.md"]
    setup.propose("Plan.md", plan.replace("third", "THIRD"), base=plan)
    setup.change_remote("Plan.md", plan.replace("first", "FIRST"))

    result = setup.run()
    assert result.returncode == 0, result.stderr
    merged = (setup.review / "Plan.md").read_text()
    assert merged == plan.replace("first", "FIRST").replace("third", "THIRD")


def test_conflict_is_shown_and_hook_blocks_markers(setup: Setup) -> None:
    """A conflict leaves markers, and the hook refuses to commit them."""
    plan = NOTES["Plan.md"]
    setup.propose("Plan.md", plan.replace("second", "proposed"), base=plan)
    setup.change_remote("Plan.md", plan.replace("second", "vault"))

    result = setup.run()
    assert result.returncode == 0, result.stderr
    assert "1 conflict" in result.stdout
    text = (setup.review / "Plan.md").read_text()
    assert "<<<<<<< current" in text
    assert ">>>>>>> proposal" in text

    setup.git(setup.review, "add", "--", "Plan.md")
    commit = subprocess.run(
        ["git", "commit", "--quiet", "-m", "markers"],
        cwd=setup.review,
        env=setup.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert commit.returncode != 0
    assert "conflict markers" in commit.stderr

    resolved = plan.replace("second", "resolved\n=======")
    (setup.review / "Plan.md").write_text(resolved)
    setup.commit_and_push("Plan.md")
    result = setup.run("--done")
    assert result.returncode == 0, result.stderr
    assert not (setup.inbox / "Plan.md").exists()


def test_new_note_whose_path_now_exists(setup: Setup) -> None:
    """Both versions appear as a conflict when the path was taken meanwhile."""
    setup.propose("Later.md", "proposed text\n")
    setup.change_remote("Later.md", "vault text\n")

    result = setup.run()
    assert result.returncode == 0, result.stderr
    assert "note added in the vault since the proposal" in result.stdout
    text = (setup.review / "Later.md").read_text()
    assert "vault text" in text
    assert "proposed text" in text
    assert "<<<<<<< current" in text


def test_edit_whose_note_was_deleted(setup: Setup) -> None:
    """An edit of a deleted note comes back as a new file."""
    garden = NOTES["Projects/Garden.md"]
    setup.propose("Projects/Garden.md", garden + "beans\n", base=garden)
    setup.change_remote("Projects/Garden.md", None)

    result = setup.run()
    assert result.returncode == 0, result.stderr
    assert "note deleted in the vault since the proposal" in result.stdout
    assert (setup.review / "Projects/Garden.md").read_text() == garden + "beans\n"
    assert setup.git(setup.review, "diff", "--name-only").strip() == (
        "Projects/Garden.md"
    )


def test_refuses_dirty_clone(setup: Setup) -> None:
    """Uncommitted work in the review clone stops the command."""
    setup.propose("New.md", "x\n")
    (setup.review / "stray.md").write_text("x\n")
    result = setup.run()
    assert result.returncode == 1
    assert "uncommitted" in result.stderr
    assert not setup.state_file().exists()


def test_refuses_foreign_hook(setup: Setup) -> None:
    """A different pre-commit hook is never replaced."""
    setup.propose("New.md", "x\n")
    hook = setup.review / ".git" / "hooks" / "pre-commit"
    _write_executable(hook, "#!/bin/sh\nexit 0\n")
    result = setup.run()
    assert result.returncode == 1
    assert "pre-commit" in result.stderr
    assert hook.read_text() == "#!/bin/sh\nexit 0\n"
    assert not (setup.review / "New.md").exists()


def test_refuses_second_application(setup: Setup) -> None:
    """A review in progress must be finished first."""
    setup.propose("New.md", "x\n")
    assert setup.run().returncode == 0
    result = setup.run()
    assert result.returncode == 1
    assert "review-proposals --done" in result.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write any file")
def test_merge_error_stops_and_application_can_restart(setup: Setup) -> None:
    """A merge-file error stops; the next run can discard the partial work.

    A read-only note makes `git merge-file` fail when it writes the result.
    """
    plan = NOTES["Plan.md"]
    setup.propose("A.md", "# A\n")
    setup.propose("Plan.md", plan.replace("first", "1st"), base=plan)
    note = setup.review / "Plan.md"
    note.chmod(0o444)
    try:
        result = setup.run()
    finally:
        note.chmod(0o644)
    assert result.returncode == 1
    assert "Plan.md" in result.stderr
    assert "pending" in setup.state_file().read_text()

    done = setup.run("--done")
    assert done.returncode == 1
    assert "review-proposals" in done.stderr
    assert setup.calls() == []

    declined = setup.run(answer="n\n")
    assert declined.returncode == 1
    assert setup.state_file().exists()

    restarted = setup.run(answer="y\n")
    assert restarted.returncode == 0, restarted.stderr
    assert (setup.review / "A.md").read_text() == "# A\n"
    assert note.read_text() == plan.replace("first", "1st")
    assert "pending" not in setup.state_file().read_text()
    assert (setup.inbox / "A.md").exists()


def test_partial_accept_reject_and_later(setup: Setup) -> None:
    """Accepted, rejected, and unfinished proposals are handled apart."""
    plan = NOTES["Plan.md"]
    garden = NOTES["Projects/Garden.md"]
    setup.propose(
        "Plan.md", plan.replace("first", "1st").replace("third", "3rd"), base=plan
    )
    setup.propose("Rejected.md", "# Rejected\n")
    setup.propose("Projects/Garden.md", garden + "beans\n", base=garden)
    assert setup.run().returncode == 0

    # Accept one of the two changes to Plan.md, reject the new note, and
    # leave the edit of Garden.md unfinished.
    (setup.review / "Plan.md").write_text(plan.replace("first", "1st"))
    setup.commit_and_push("Plan.md")
    setup.git(setup.review, "reset", "--quiet", "--", "Rejected.md")
    (setup.review / "Rejected.md").unlink()

    declined = setup.run("--done", answer="n\n")
    assert declined.returncode == 1
    assert "Projects/Garden.md" in declined.stdout
    assert setup.calls() == []
    assert (setup.inbox / "Plan.md").exists()

    result = setup.run("--done", answer="y\n")
    assert result.returncode == 0, result.stderr
    assert not (setup.inbox / "Plan.md").exists()
    assert not (setup.inbox / ".base" / "Plan.md").exists()
    assert not (setup.inbox / "Rejected.md").exists()
    assert (setup.inbox / "Projects/Garden.md").exists()
    assert (setup.inbox / ".base" / "Projects/Garden.md").exists()
    assert _status(setup) == ""
    assert not setup.state_file().exists()


def test_proposal_changed_during_review_stays(setup: Setup) -> None:
    """A proposal replaced after it was applied is not deleted."""
    setup.propose("New.md", "first version\n")
    assert setup.run().returncode == 0
    setup.propose("New.md", "second version\n")
    setup.commit_and_push("New.md")

    result = setup.run("--done")
    assert result.returncode == 0, result.stderr
    assert "changed during the review" in result.stdout
    assert (setup.inbox / "New.md").read_text() == "second version\n"


def test_refuses_commits_that_did_not_reach_the_served_branch(setup: Setup) -> None:
    """Unpushed commits, or commits on another branch, keep every proposal."""
    setup.propose("New.md", "x\n")
    assert setup.run().returncode == 0
    setup.git(setup.review, "add", "--", "New.md")
    setup.git(setup.review, "commit", "--quiet", "-m", "accept")

    unpushed = setup.run("--done")
    assert unpushed.returncode == 1
    assert "Push them" in unpushed.stderr

    setup.git(setup.review, "push", "--quiet", "origin", "HEAD:other")
    other_branch = setup.run("--done")
    assert other_branch.returncode == 1
    assert setup.calls() == []
    assert (setup.inbox / "New.md").exists()


def test_interrupted_restore_of_later_notes(setup: Setup) -> None:
    """Notes recorded as `later` are restored again and their proposals kept.

    The state file is edited to look like an interruption after the `later`
    status was recorded, when one note was restored and the other was not.
    """
    plan = NOTES["Plan.md"]
    setup.propose("Plan.md", plan.replace("first", "1st"), base=plan)
    setup.propose("New.md", "# New\n")
    assert setup.run().returncode == 0
    state = setup.state_file()
    state.write_text(state.read_text().replace("applied\t", "later\t"))
    setup.git(setup.review, "restore", "--source=HEAD", "--", "Plan.md")

    result = setup.run("--done")
    assert result.returncode == 0, result.stderr
    assert _status(setup) == ""
    assert (setup.inbox / "Plan.md").exists()
    assert (setup.inbox / "New.md").exists()
    assert not state.exists()


def test_failed_stop_keeps_state_for_a_retry(setup: Setup) -> None:
    """If the server cannot be stopped, nothing is deleted and a retry works."""
    setup.propose("New.md", "x\n")
    setup.propose("Later.md", "y\n")
    assert setup.run().returncode == 0
    setup.commit_and_push("New.md")
    (setup.tmp / "fail-stop").touch()

    failed = setup.run("--done", answer="y\n")
    assert failed.returncode == 1
    assert (setup.inbox / "New.md").exists()
    assert "later\t" in setup.state_file().read_text()
    assert _status(setup) == ""

    (setup.tmp / "fail-stop").unlink()
    retried = setup.run("--done")
    assert retried.returncode == 0, retried.stderr
    assert not (setup.inbox / "New.md").exists()
    assert (setup.inbox / "Later.md").exists()


def test_interrupted_deletion_completes(setup: Setup) -> None:
    """A proposal deleted before its base copy counts as deleted."""
    plan = NOTES["Plan.md"]
    setup.propose("Plan.md", plan.replace("first", "1st"), base=plan)
    assert setup.run().returncode == 0
    setup.commit_and_push("Plan.md")
    (setup.inbox / "Plan.md").unlink()

    result = setup.run("--done")
    assert result.returncode == 0, result.stderr
    assert not (setup.inbox / ".base" / "Plan.md").exists()


def test_records_hashes_of_applied_text(setup: Setup) -> None:
    """The state file holds the hashes of the proposal and its base copy."""
    plan = NOTES["Plan.md"]
    setup.propose("Plan.md", plan.replace("first", "1st"), base=plan)
    setup.propose("New.md", "x\n")
    assert setup.run().returncode == 0
    lines = sorted(setup.state_file().read_text().splitlines())
    assert lines == sorted(
        [
            "\t".join(
                [
                    "applied",
                    _sha256(setup.inbox / "Plan.md"),
                    _sha256(setup.inbox / ".base" / "Plan.md"),
                    "Plan.md",
                ]
            ),
            "\t".join(["applied", _sha256(setup.inbox / "New.md"), "-", "New.md"]),
        ]
    )


def test_note_names_are_literal(tmp_path: Path) -> None:
    """A name with `*` never touches the notes that the pattern would match."""
    notes = {"notes/*.md": "star\n", "notes/ab.md": "ab\n", "notes/ac.md": "ac\n"}
    setup = _make_setup(tmp_path, notes)
    setup.propose("notes/*.md", "star changed\n", base="star\n")
    setup.propose("notes/ab.md", "ab changed\n", base="ab\n")
    assert setup.run().returncode == 0
    setup.commit_and_push("notes/ab.md")
    # An unrelated edit that a pattern restore of notes/*.md would discard.
    (setup.review / "notes/ac.md").write_text("ac edited\n")

    result = setup.run("--done", answer="y\n")
    assert result.returncode == 0, result.stderr
    assert (setup.review / "notes/ac.md").read_text() == "ac edited\n"
    assert (setup.review / "notes/*.md").read_text() == "star\n"
    assert (setup.inbox / "notes/*.md").exists()
    assert not (setup.inbox / "notes/ab.md").exists()


def test_subtree_root(tmp_path: Path) -> None:
    """With a subtree as the root, proposals apply below that subtree."""
    setup = _make_setup(tmp_path, NOTES, subtree="vault")
    setup.propose("Ideas/New.md", "# New\n")
    result = setup.run()
    assert result.returncode == 0, result.stderr
    assert (setup.review / "vault/Ideas/New.md").read_text() == "# New\n"
    setup.commit_and_push("vault/Ideas/New.md")
    assert setup.run("--done").returncode == 0
    assert not (setup.inbox / "Ideas").exists()


def test_failed_start_keeps_state_for_a_retry(setup: Setup) -> None:
    """If the server does not start, the command says how and can be rerun."""
    setup.propose("New.md", "x\n")
    assert setup.run().returncode == 0
    setup.commit_and_push("New.md")
    (setup.tmp / "fail-start").touch()

    failed = setup.run("--done")
    assert failed.returncode == 1
    assert "sudo systemctl start knowledge-server" in failed.stderr
    assert setup.state_file().exists()

    (setup.tmp / "fail-start").unlink()
    retried = setup.run("--done")
    assert retried.returncode == 0, retried.stderr
    assert setup.calls()[-1] == "start knowledge-server"
    assert not setup.state_file().exists()


def test_failed_deletion_restarts_the_server(setup: Setup) -> None:
    """A failed deletion still starts the server and keeps the state file."""
    garden = NOTES["Projects/Garden.md"]
    setup.propose("Projects/Garden.md", garden + "beans\n", base=garden)
    setup.propose("Later.md", "y\n")
    assert setup.run().returncode == 0
    setup.commit_and_push("Projects/Garden.md")
    (setup.tmp / "fail-rm").touch()

    failed = setup.run("--done", answer="y\n")
    assert failed.returncode == 1
    assert setup.calls() == ["stop knowledge-server", "start knowledge-server"]
    assert (setup.inbox / "Projects/Garden.md").exists()
    assert setup.state_file().exists()

    (setup.tmp / "fail-rm").unlink()
    retried = setup.run("--done")
    assert retried.returncode == 0, retried.stderr
    assert not (setup.inbox / "Projects/Garden.md").exists()
    assert not (setup.inbox / ".base/Projects/Garden.md").exists()
    assert (setup.inbox / "Later.md").exists()


def test_failed_git_status_never_counts_as_clean(setup: Setup) -> None:
    """A failed `git status` stops both commands instead of looking clean."""
    real_git = shutil.which("git")
    assert real_git is not None
    _write_executable(
        setup.tmp / "bin" / "git",
        "#!/bin/sh\n"
        "# Fail `git status` while the marker file exists.\n"
        'for arg; do [ "$arg" = status ] && [ -e "$GIT_FAIL_STATUS" ] && exit 128; done\n'
        f'exec {real_git} "$@"\n',
    )
    marker = setup.tmp / "fail-status"
    setup.env["GIT_FAIL_STATUS"] = str(marker)
    setup.propose("New.md", "x\n")
    marker.touch()
    refused = setup.run()
    assert refused.returncode == 1
    assert "git status failed" in refused.stderr
    assert not setup.state_file().exists()
    marker.unlink()

    assert setup.run().returncode == 0
    setup.commit_and_push("New.md")
    marker.touch()
    result = setup.run("--done")
    assert result.returncode == 1
    assert "git status failed" in result.stderr
    assert setup.calls() == []
    assert (setup.inbox / "New.md").exists()


def test_matching_hook_is_made_executable(setup: Setup) -> None:
    """A hook left without execute permission is repaired."""
    setup.propose("New.md", "x\n")
    assert setup.run().returncode == 0
    hook = setup.review / ".git" / "hooks" / "pre-commit"
    setup.git(setup.review, "reset", "--quiet", "--", "New.md")
    (setup.review / "New.md").unlink()
    setup.state_file().unlink()
    hook.chmod(0o644)

    assert setup.run().returncode == 0
    assert os.access(hook, os.X_OK)
