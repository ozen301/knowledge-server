"""Shared component-wise path validation and immediate discovery policy."""

import os
import stat
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from knowledge_server.core.limits import DEFAULT_LIMITS
from knowledge_server.core.models import DomainErrorCode, KnowledgeError


class TargetKind(StrEnum):
    """The filesystem object kind an operation accepts."""

    FILE = "file"
    DIRECTORY = "directory"
    EITHER = "either"


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    """A visible, non-symlink path resolved from a root-relative API path."""

    path: Path
    relative_path: str
    kind: TargetKind


@dataclass(frozen=True, slots=True)
class VisibleEntry:
    """A visible eligible immediate directory child."""

    relative_path: str
    kind: TargetKind


class PathPolicy:
    """Apply the Phase 1 visibility policy under one resolved vault root."""

    def __init__(
        self,
        root: Path,
        *,
        immediate_entry_limit: int = DEFAULT_LIMITS.max_immediate_directory_entries,
    ) -> None:
        self.root = root.resolve(strict=True)
        self.immediate_entry_limit = immediate_entry_limit

    def resolve(self, api_path: str, target: TargetKind) -> ResolvedPath:
        """Resolve one API path after lexical, visibility, and type checks."""
        components, trailing_slash = self._parse(api_path, target)
        if not components:
            return ResolvedPath(self.root, "", TargetKind.DIRECTORY)
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
        elif actual_kind is TargetKind.FILE and not current.name.lower().endswith(
            ".md"
        ):
            raise KnowledgeError(DomainErrorCode.UNSUPPORTED_TYPE)
        return ResolvedPath(current, "/".join(components), actual_kind)

    def discover_immediate(self, api_path: str) -> tuple[VisibleEntry, ...]:
        """Return visible immediate children after counting every inspected entry."""
        directory = self.resolve(api_path, TargetKind.DIRECTORY)
        entries: list[VisibleEntry] = []
        try:
            with os.scandir(directory.path) as scan:
                for count, entry in enumerate(scan, start=1):
                    if count > self.immediate_entry_limit:
                        raise KnowledgeError(DomainErrorCode.DIRECTORY_LIMIT_EXCEEDED)
                    visible = self._visible_entry(entry.name, directory.relative_path)
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
        if not isinstance(api_path, str):
            raise KnowledgeError(DomainErrorCode.INVALID_PATH)
        if not api_path:
            if target is TargetKind.FILE:
                raise KnowledgeError(DomainErrorCode.INVALID_PATH)
            return [], False
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
        try:
            return path.lstat().st_mode
        except FileNotFoundError:
            raise KnowledgeError(DomainErrorCode.NOT_FOUND) from None
        except OSError:
            raise KnowledgeError(DomainErrorCode.ACCESS_DENIED) from None

    def _visible_entry(self, name: str, parent: str) -> VisibleEntry | None:
        """Return an entry only when ordinary direct resolution accepts it."""
        relative_path = f"{parent}/{name}" if parent else name
        try:
            resolved = self.resolve(relative_path, TargetKind.EITHER)
        except KnowledgeError:
            return None
        return VisibleEntry(relative_path, resolved.kind)
