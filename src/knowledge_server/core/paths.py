"""The path and visibility policy that every tool applies.

Callers name notes and directories by their path relative to the vault root,
using "/" as the separator: `Projects/roadmap.md` means the file
`roadmap.md` in the `Projects` directory of the vault, and an empty path means
the root itself. This module calls these root-relative paths.

`PathPolicy` turns a root-relative path into a checked filesystem path, or
raises a `KnowledgeError` with the contract's error code. It checks each path
component separately and rejects any symlink it finds below the root, so a
path cannot lead outside the vault. Hidden names (starting with ".") and
non-Markdown files are never visible.

The checks inspect the filesystem at one moment; a file or directory replaced
afterward is not detected here. Code that opens files must guard against that
itself, as `reader.load_note` does.
"""

import os
import stat
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from knowledge_server.core.limits import DEFAULT_LIMITS
from knowledge_server.core.models import DomainErrorCode, KnowledgeError


class TargetKind(StrEnum):
    """The kind of filesystem object an operation accepts or found.

    `EITHER` is used only for requests that accept a file or a directory, such
    as search. A resolved path is always a `FILE` or a `DIRECTORY`.
    """

    FILE = "file"
    DIRECTORY = "directory"
    EITHER = "either"


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    """A visible path that passed the policy checks.

    Attributes:
        path: Absolute filesystem path below the root.
        relative_path: Normalized root-relative path, without a trailing slash;
            empty for the root itself.
        kind: `FILE` or `DIRECTORY`.
    """

    path: Path
    relative_path: str
    kind: TargetKind


@dataclass(frozen=True, slots=True)
class VisibleEntry:
    """A visible immediate child found while listing a directory.

    Attributes:
        relative_path: Root-relative path of the child.
        kind: `FILE` or `DIRECTORY`.
    """

    relative_path: str
    kind: TargetKind


class PathPolicy:
    """Apply the path and visibility policy under one vault root.

    Attributes:
        root: The vault root, fully resolved when the policy is created.
        immediate_entry_limit: Most entries `discover_immediate` scans in one
            directory.
    """

    def __init__(
        self,
        root: Path,
        *,
        immediate_entry_limit: int = DEFAULT_LIMITS.max_immediate_directory_entries,
    ) -> None:
        """Create a policy for a vault root.

        Args:
            root: Existing vault root directory. A symlink is resolved here,
                once.
            immediate_entry_limit: Most entries to scan in one directory.

        Raises:
            OSError: If the root does not exist or cannot be resolved.
        """
        self.root = root.resolve(strict=True)
        self.immediate_entry_limit = immediate_entry_limit

    def resolve(self, api_path: str, target: TargetKind) -> ResolvedPath:
        """Check a root-relative path and return where it points.

        The checks run in the contract's order: the path's form first, then
        hidden names, and only then the filesystem. This order keeps hidden or
        disallowed paths from revealing whether they exist. A `FILE` target's
        Markdown suffix is checked before the filesystem too; an `EITHER`
        target's suffix is checked after, because only the filesystem shows
        whether the path is a file or a directory.

        Args:
            api_path: Path relative to the root, using "/". Empty means the
                root and is invalid when `target` is `FILE`.
            target: The kind of object the operation accepts.

        Returns:
            The checked path.

        Raises:
            KnowledgeError: With `INVALID_PATH`, `ACCESS_DENIED`,
                `UNSUPPORTED_TYPE`, `NOT_FOUND`, `NOT_A_FILE`, or
                `NOT_A_DIRECTORY` as the contract's error mappings define.
        """
        components, trailing_slash = self._parse(api_path, target)
        if not components:
            return ResolvedPath(self.root, "", TargetKind.DIRECTORY)
        # Inspect each component with lstat before descending into it, so a
        # symlink anywhere in the path is rejected instead of followed.
        current = self.root
        for index, component in enumerate(components):
            is_final = index == len(components) - 1
            current = current / component
            mode = self._lstat_mode(current)
            if stat.S_ISLNK(mode) or not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise KnowledgeError(DomainErrorCode.ACCESS_DENIED)
            self._check_contained(current)
            if not is_final and not stat.S_ISDIR(mode):
                raise KnowledgeError(DomainErrorCode.NOT_A_DIRECTORY)

        actual_kind = TargetKind.DIRECTORY if stat.S_ISDIR(mode) else TargetKind.FILE
        if trailing_slash and actual_kind is not TargetKind.DIRECTORY:
            raise KnowledgeError(DomainErrorCode.NOT_A_DIRECTORY)
        if target is TargetKind.FILE:
            if actual_kind is TargetKind.DIRECTORY:
                raise KnowledgeError(DomainErrorCode.NOT_A_FILE)
        elif target is TargetKind.DIRECTORY:
            if actual_kind is TargetKind.FILE:
                raise KnowledgeError(DomainErrorCode.NOT_A_DIRECTORY)
        # For an EITHER target, any visible directory is accepted, but a file
        # must still be Markdown. (For FILE targets, _parse checked the suffix.)
        elif actual_kind is TargetKind.FILE and not current.name.lower().endswith(
            ".md"
        ):
            raise KnowledgeError(DomainErrorCode.UNSUPPORTED_TYPE)
        return ResolvedPath(current, "/".join(components), actual_kind)

    def discover_immediate(self, api_path: str) -> tuple[VisibleEntry, ...]:
        """Return a directory's visible immediate children, sorted by path.

        Each child is checked with `resolve`, so listing and direct access
        always agree on what is visible.

        Args:
            api_path: Root-relative directory path.

        Returns:
            The visible children, sorted by root-relative path in code-point
            order.

        Raises:
            KnowledgeError: With the codes `resolve` raises for the directory,
                or `DIRECTORY_LIMIT_EXCEEDED` when the directory has more than
                `immediate_entry_limit` entries, counting hidden ones.
        """
        directory = self.resolve(api_path, TargetKind.DIRECTORY)
        entries: list[VisibleEntry] = []
        try:
            with os.scandir(directory.path) as scan:
                for count, entry in enumerate(scan, start=1):
                    if count > self.immediate_entry_limit:
                        raise KnowledgeError(DomainErrorCode.DIRECTORY_LIMIT_EXCEEDED)
                    visible = self.visible_child(directory.relative_path, entry.name)
                    if visible is not None:
                        entries.append(visible)
        except KnowledgeError:
            raise
        except FileNotFoundError:
            raise KnowledgeError(DomainErrorCode.NOT_FOUND) from None
        except OSError:
            raise KnowledgeError(DomainErrorCode.ACCESS_DENIED) from None
        return tuple(sorted(entries, key=lambda entry: entry.relative_path))

    def _parse(self, api_path: str, target: TargetKind) -> tuple[list[str], bool]:
        """Check a path's form without touching the filesystem.

        Returns:
            The path components and whether the path had a trailing slash.
        """
        if not isinstance(api_path, str):
            raise KnowledgeError(DomainErrorCode.INVALID_PATH)
        if not api_path:
            if target is TargetKind.FILE:
                raise KnowledgeError(DomainErrorCode.INVALID_PATH)
            return [], False
        # Reject absolute POSIX paths, Windows UNC paths (\\server\share) and
        # drive paths ("C:"), backslashes, and control characters.
        if (
            api_path.startswith(("/", "\\\\"))
            or (
                len(api_path) >= 2
                and api_path[0]
                in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                and api_path[1] == ":"
            )
            or "\\" in api_path
            or any(unicodedata.category(char) == "Cc" for char in api_path)
        ):
            raise KnowledgeError(DomainErrorCode.INVALID_PATH)
        trailing_slash = api_path.endswith("/")
        if trailing_slash:
            if target is TargetKind.FILE or api_path.endswith("//"):
                raise KnowledgeError(DomainErrorCode.INVALID_PATH)
            api_path = api_path[:-1]
        components = api_path.split("/")
        if any(not component or component in {".", ".."} for component in components):
            raise KnowledgeError(DomainErrorCode.INVALID_PATH)
        if any(component.startswith(".") for component in components):
            raise KnowledgeError(DomainErrorCode.ACCESS_DENIED)
        if target is TargetKind.FILE and not components[-1].lower().endswith(".md"):
            raise KnowledgeError(DomainErrorCode.UNSUPPORTED_TYPE)
        return components, trailing_slash

    def _check_contained(self, path: Path) -> None:
        """Confirm a checked component remains below the resolved configured root."""
        try:
            path.resolve(strict=True).relative_to(self.root)
        except FileNotFoundError:
            raise KnowledgeError(DomainErrorCode.NOT_FOUND) from None
        except OSError, ValueError:
            raise KnowledgeError(DomainErrorCode.ACCESS_DENIED) from None

    @staticmethod
    def _lstat_mode(path: Path) -> int:
        """Return a path's file mode without following a symlink."""
        try:
            return path.lstat().st_mode
        except FileNotFoundError:
            raise KnowledgeError(DomainErrorCode.NOT_FOUND) from None
        except OSError:
            raise KnowledgeError(DomainErrorCode.ACCESS_DENIED) from None

    def visible_child(self, parent: str, name: str) -> VisibleEntry | None:
        """Check one directory entry found by scanning a visible directory.

        The entry is checked with `resolve` from the root, so scanning and
        direct access always agree on what is visible. If the parent was
        replaced by a symlink after it was checked, its entries are rejected
        here, so no name from outside the root is returned.

        Args:
            parent: Root-relative path of the scanned directory; empty for the
                root.
            name: The entry's name in that directory.

        Returns:
            The visible entry, or None when the policy hides or rejects it.
        """
        relative_path = f"{parent}/{name}" if parent else name
        try:
            resolved = self.resolve(relative_path, TargetKind.EITHER)
        except KnowledgeError:
            return None
        return VisibleEntry(relative_path, resolved.kind)
