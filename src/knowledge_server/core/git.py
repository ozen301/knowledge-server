"""Bounded Git access to proposal branches with an independent object store."""

import contextlib
import hashlib
import os
import selectors
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path, PurePosixPath

from knowledge_server.core.limits import DEFAULT_LIMITS, Limits
from knowledge_server.core.models import (
    DomainErrorCode,
    KnowledgeError,
    ListProposalsResult,
    ProposalEntry,
    ProposalReadRequest,
    ReadResult,
    validate_proposal,
)
from knowledge_server.core.paths import PathPolicy, TargetKind
from knowledge_server.core.reader import decode_note_bytes, format_read, read_end

LOCAL_DEADLINE_SECONDS = 10.0
NETWORK_DEADLINE_SECONDS = 30.0
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
# Fetches share refs with writes. Serialize them too so a fetched OID always
# belongs to the fetch that selected it.
PROPOSAL_LOCK = threading.Lock()
VAULT_REMOTE_KEY = "/etc/knowledge-server/vault-remote-key"
KNOWN_HOSTS_FILE = "/etc/knowledge-server/known_hosts"
SSH_COMMAND = (
    f"ssh -i {VAULT_REMOTE_KEY} -o IdentitiesOnly=yes "
    f"-o BatchMode=yes -o UserKnownHostsFile={KNOWN_HOSTS_FILE}"
)
CHECKOUT_REF = "refs/knowledge/checkout"


def run_git(
    directory: Path,
    arguments: list[str],
    *,
    data: bytes = b"",
    network: bool = False,
    extra_env: dict[str, str] | None = None,
    checkout: bool = False,
) -> tuple[int, bytes]:
    """Run Git without prompts, with bounded output and a fixed deadline.

    User, system, and inherited Git configuration is excluded. Explicit
    environment overrides supply the SSH command for remote operations.
    Child processes are killed and reaped on timeout or output overflow.

    Returns:
        Exit status and stdout; stderr is counted but never exposed.

    Raises:
        KnowledgeError: `GIT_FAILED` if Git cannot run or exceeds a bound.
    """
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL="/dev/null",
        GIT_TERMINAL_PROMPT="0",
        GIT_AUTHOR_NAME="knowledge-server",
        GIT_AUTHOR_EMAIL="knowledge-server@example.invalid",
        GIT_COMMITTER_NAME="knowledge-server",
        GIT_COMMITTER_EMAIL="knowledge-server@example.invalid",
        GIT_LITERAL_PATHSPECS="1",
        LC_ALL="C",
    )
    environment.update(extra_env or {})
    command = ["git", "-C", str(directory)]
    if checkout:
        command += ["-c", f"safe.directory={directory}"]
    command += arguments
    deadline = time.monotonic() + (
        NETWORK_DEADLINE_SECONDS if network else LOCAL_DEADLINE_SECONDS
    )
    try:
        # A file avoids blocking on a pipe while Git emits output before
        # consuming all input. Temporary input is private and always removed.
        with tempfile.TemporaryFile() as source:
            source.write(data)
            source.seek(0)
            with subprocess.Popen(
                command,
                stdin=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                start_new_session=True,
            ) as process:
                assert process.stdout is not None and process.stderr is not None
                output = bytearray()
                used = 0
                try:
                    with selectors.DefaultSelector() as selector:
                        selector.register(process.stdout, selectors.EVENT_READ, True)
                        selector.register(process.stderr, selectors.EVENT_READ, False)
                        while selector.get_map():
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise KnowledgeError(DomainErrorCode.GIT_FAILED)
                            for key, _ in selector.select(remaining):
                                chunk = os.read(
                                    key.fd, min(65536, MAX_OUTPUT_BYTES - used + 1)
                                )
                                if not chunk:
                                    selector.unregister(key.fileobj)
                                    continue
                                used += len(chunk)
                                if used > MAX_OUTPUT_BYTES:
                                    raise KnowledgeError(DomainErrorCode.GIT_FAILED)
                                if key.data:
                                    output.extend(chunk)
                    status = process.wait(
                        timeout=max(0.001, deadline - time.monotonic())
                    )
                except BaseException:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise
                return status, bytes(output)
    except OSError, subprocess.TimeoutExpired:
        raise KnowledgeError(DomainErrorCode.GIT_FAILED) from None


class ProposalBranches:
    """Manage unreviewed branches in a server-owned bare object store.

    Attributes:
        policy: Shared policy for root-relative note paths.
        repository: Bare object store under the server's state directory.
        vault_remote: URL passed explicitly to remote fetches and pushes.
        ssh_command: SSH command for remote operations, or None for local tests.
        limits: Note, read, and listing limits.
        checkout: Top-level of the local vault checkout.
        prefix: Root's prefix within that checkout, with a trailing slash.
    """

    def __init__(
        self,
        policy: PathPolicy,
        vault_remote: str,
        *,
        state_directory: Path | None = None,
        limits: Limits = DEFAULT_LIMITS,
        ssh_command: str | None = SSH_COMMAND,
    ) -> None:
        """Initialize the object store and import the checkout's HEAD locally.

        Use STATE_DIRECTORY unless a state directory is supplied explicitly.
        The directory must be absolute and usable; it is created if absent.
        Startup never contacts the vault remote.

        Raises:
            KnowledgeError: `GIT_FAILED` if state or the checkout is unusable.
        """
        self.policy = policy
        self.vault_remote = vault_remote
        self.ssh_command = ssh_command
        self.limits = limits
        if state_directory is None:
            value = os.environ.get("STATE_DIRECTORY")
            if not value:
                raise KnowledgeError(DomainErrorCode.GIT_FAILED)
            state_directory = Path(value)
        if not state_directory.is_absolute():
            raise KnowledgeError(DomainErrorCode.GIT_FAILED)
        self.repository = state_directory / "vault.git"
        # Git checks ownership against the checkout top-level, including
        # when the served root is a subtree. Find the nearest .git marker
        # before invoking Git with that exact safe.directory exception.
        candidate = next(
            (
                directory
                for directory in (policy.root, *policy.root.parents)
                if (directory / ".git").exists()
            ),
            None,
        )
        if candidate is None:
            raise KnowledgeError(DomainErrorCode.GIT_FAILED)
        status, output = run_git(
            candidate, ["rev-parse", "--show-toplevel"], checkout=True
        )
        if status:
            raise KnowledgeError(DomainErrorCode.GIT_FAILED)
        self.checkout = Path(os.fsdecode(output.removesuffix(b"\n"))).resolve(
            strict=True
        )
        prefix = policy.root.relative_to(self.checkout).as_posix()
        self.prefix = "" if prefix == "." else prefix + "/"
        if self.repository.resolve().is_relative_to(self.checkout):
            raise KnowledgeError(DomainErrorCode.ACCESS_DENIED)
        try:
            state_directory.mkdir(parents=True, exist_ok=True)
            status, _ = run_git(
                state_directory, ["init", "--bare", str(self.repository)]
            )
            if status:
                raise KnowledgeError(DomainErrorCode.GIT_FAILED)
            with PROPOSAL_LOCK:
                self.import_checkout()
        except OSError, ValueError:
            raise KnowledgeError(DomainErrorCode.GIT_FAILED) from None

    def command(
        self,
        *arguments: str,
        data: bytes = b"",
        network: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> bytes:
        """Run a bounded command in the internal object store and require success.

        Returns:
            Git stdout.
        """
        status, output = run_git(
            self.repository,
            list(arguments),
            data=data,
            network=network,
            extra_env=extra_env,
        )
        if status:
            raise KnowledgeError(DomainErrorCode.GIT_FAILED)
        return output

    def base(self, proposal: str | None) -> str:
        """Fetch an existing proposal tip, or import the local checkout's HEAD.

        Returns:
            The selected commit's object ID.
        """
        if proposal is not None:
            validate_proposal(proposal)
            self.command(
                "fetch",
                "--prune",
                "--no-tags",
                self.vault_remote,
                "+refs/heads/proposal/*:refs/vault/proposal/*",
                network=True,
                extra_env=self._remote_environment(),
            )
            status, output = run_git(
                self.repository,
                ["rev-parse", "--verify", f"refs/vault/{proposal}^{{commit}}"],
            )
            if status:
                raise KnowledgeError(DomainErrorCode.NOT_FOUND)
        else:
            self.import_checkout()
            output = self.command("rev-parse", "--verify", f"{CHECKOUT_REF}^{{commit}}")
        return output.decode("ascii").strip()

    def import_checkout(self) -> None:
        """Copy HEAD and its history into one internal ref without network.

        Call under PROPOSAL_LOCK. The longer deadline accommodates the initial
        history transfer; remote SSH settings are not used for this fetch.
        """
        self.command(
            "-c",
            f"safe.directory={self.checkout}",
            "fetch",
            "--no-tags",
            str(self.checkout),
            f"+HEAD:{CHECKOUT_REF}",
            network=True,
        )

    def _remote_environment(self) -> dict[str, str] | None:
        return (
            {"GIT_SSH_COMMAND": self.ssh_command}
            if self.ssh_command is not None
            else None
        )

    def entry(
        self, commit: str, path: str, *, absent_ok: bool = False
    ) -> tuple[str, str] | None:
        """Check each commit path component and return the final mode and OID.

        Missing parents are accepted only for a new note. Existing symlinks
        and special modes are denied before file/directory checks.

        Returns:
            Mode and object ID, or None for an accepted missing entry.
        """
        components, _ = self.policy.parse(path, TargetKind.FILE)
        components = (
            self.prefix.rstrip("/").split("/") + components
            if self.prefix
            else components
        )
        current: list[str] = []
        for index, component in enumerate(components):
            current.append(component)
            raw = self.command("ls-tree", "-z", commit, "--", "/".join(current))
            if not raw:
                if absent_ok:
                    return None
                raise KnowledgeError(DomainErrorCode.NOT_FOUND)
            metadata, _ = raw.rstrip(b"\0").split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split()
            if mode not in {"040000", "100644", "100755"} or kind not in {
                "tree",
                "blob",
            }:
                raise KnowledgeError(DomainErrorCode.ACCESS_DENIED)
            if index < len(components) - 1 and mode != "040000":
                raise KnowledgeError(DomainErrorCode.NOT_A_DIRECTORY)
        return mode, oid

    def load(
        self, commit: str, path: str, limits: Limits | None = None
    ) -> tuple[bytes, str]:
        """Load a regular commit blob through the shared note content checks.

        Returns:
            Raw bytes and decoded text, with BOM removed and CRLF normalized.
        """
        limits = self.limits if limits is None else limits
        entry = self.entry(commit, path)
        assert entry is not None
        mode, oid = entry
        if mode == "040000":
            raise KnowledgeError(DomainErrorCode.NOT_A_FILE)
        size = int(self.command("cat-file", "-s", oid))
        if size > limits.max_file_bytes:
            raise KnowledgeError(DomainErrorCode.FILE_TOO_LARGE)
        # Git objects are immutable: the size and data commands refer to the
        # same OID. The runner additionally bounds stdout and stderr.
        return decode_note_bytes(self.command("cat-file", "blob", oid), limits)

    def read(self, request: ProposalReadRequest) -> ReadResult:
        """Fetch and read a proposal note with the root reader's limits.

        Returns:
            The usual numbered read result and raw-byte hash.
        """
        read_end(request, self.limits)
        assert request.proposal is not None
        validate_proposal(request.proposal)
        self.policy.parse(request.path, TargetKind.FILE)
        with PROPOSAL_LOCK:
            raw, text = self.load(self.base(request.proposal), request.path)
        lines = tuple(text.removesuffix("\n").split("\n")) if text else ()
        return format_read(request, lines, hashlib.sha256(raw).hexdigest(), self.limits)

    def commit_and_push(
        self,
        base: str,
        proposal: str,
        path: str,
        data: bytes,
        *,
        revision: bool,
        summary: str,
    ) -> None:
        """Create one unreviewed commit and push it with an explicit ref lease.

        Keep an existing note's mode; new notes use mode 100644.

        Raises:
            KnowledgeError: `ALREADY_EXISTS` or `STALE_CONTENT` on a refused
                lease, or `GIT_FAILED` for other failures.
        """
        validate_proposal(proposal)
        entry = self.entry(base, path, absent_ok=True)
        mode = entry[0] if entry is not None else "100644"
        blob = self.command("hash-object", "-w", "--stdin", data=data).decode().strip()
        with tempfile.TemporaryDirectory(prefix="knowledge-proposal-") as temporary:
            environment = {"GIT_INDEX_FILE": str(Path(temporary) / "index")}
            self.command("read-tree", base, extra_env=environment)
            self.command(
                "update-index",
                "--add",
                "--cacheinfo",
                f"{mode},{blob},{self.prefix}{path}",
                extra_env=environment,
            )
            tree = self.command("write-tree", extra_env=environment).decode().strip()
        commit = (
            self.command(
                "commit-tree",
                tree,
                "-p",
                base,
                data=f"Unreviewed proposal: {summary}\n".encode(),
            )
            .decode()
            .strip()
        )
        ref = f"refs/heads/{proposal}"
        lease = base if revision else ""
        status, output = run_git(
            self.repository,
            [
                "push",
                "--porcelain",
                f"--force-with-lease={ref}:{lease}",
                self.vault_remote,
                f"{commit}:{ref}",
            ],
            network=True,
            extra_env=self._remote_environment(),
        )
        if status:
            # Porcelain stdout distinguishes a refused lease from a remote
            # hook or transport failure without exposing Git diagnostics.
            if b"[rejected] (stale info)" in output:
                raise KnowledgeError(
                    DomainErrorCode.STALE_CONTENT
                    if revision
                    else DomainErrorCode.ALREADY_EXISTS
                )
            raise KnowledgeError(DomainErrorCode.GIT_FAILED)

    def list_proposals(self) -> ListProposalsResult:
        """Fetch main and proposals with prune and list changed visible notes.

        Returns:
            Branches and their root-relative changed note paths, sorted.
        """
        with PROPOSAL_LOCK:
            self.command(
                "fetch",
                "--prune",
                "--no-tags",
                self.vault_remote,
                "+refs/heads/main:refs/vault/main",
                "+refs/heads/proposal/*:refs/vault/proposal/*",
                network=True,
                extra_env=self._remote_environment(),
            )
            branches = self.command(
                "for-each-ref",
                "--format=%(refname) %(objectname)",
                "refs/vault/proposal/",
            ).splitlines()
            used = len(branches)
            self._listing_budget(used)
            proposals: list[ProposalEntry] = []
            for branch in branches:
                ref, tip = os.fsdecode(branch).split()
                name = ref.removeprefix("refs/vault/")
                try:
                    validate_proposal(name)
                except KnowledgeError:
                    continue
                base = (
                    self.command("merge-base", "refs/vault/main", tip).decode().strip()
                )
                paths = self.command(
                    "diff",
                    "--no-ext-diff",
                    "--no-renames",
                    "--name-status",
                    "-z",
                    base,
                    tip,
                    "--",
                ).split(b"\0")[:-1]
                used += len(paths) // 2
                self._listing_budget(used)
                visible: list[str] = []
                for status, raw in zip(paths[::2], paths[1::2], strict=True):
                    full_path = PurePosixPath(os.fsdecode(raw))
                    try:
                        path = full_path.relative_to(self.prefix).as_posix()
                    except ValueError:
                        continue
                    try:
                        entry = self.entry(base if status == b"D" else tip, path)
                        if entry is not None and entry[0] in {"100644", "100755"}:
                            visible.append(path)
                    except KnowledgeError as error:
                        if error.code == DomainErrorCode.GIT_FAILED:
                            raise
                proposals.append(ProposalEntry(proposal=name, paths=sorted(visible)))
        return ListProposalsResult(
            proposals=sorted(proposals, key=lambda item: item.proposal)
        )

    def _listing_budget(self, used: int) -> None:
        if used > self.limits.max_proposal_entries:
            raise KnowledgeError(DomainErrorCode.DIRECTORY_LIMIT_EXCEEDED)
