"""Startup guard for the dedicated invented-note HTTP trial scope."""

import hashlib
import json
import os
import stat
from importlib.resources import files
from pathlib import Path

from knowledge_server.config import ConfigurationError


def verify_synthetic_root(root: Path) -> None:
    """Check that a directory holds exactly the packaged invented notes.

    The packaged manifest, `synthetic-vault.json`, lists each sample note's
    relative path and SHA-256 digest. The regular files under the directory
    must match it exactly: none may be missing, changed, or extra.
    Subdirectories are scanned but are not listed in the manifest. The caller
    supplies the root explicitly; no fixture location or environment variable
    is used. The root itself must not be a symlink, and a symlink or special
    file found by the scan is rejected. The scan fails on more than 100
    entries, directories included, or on a file larger than 1 MiB. It checks
    the contents only when called at startup, so the directory must stay
    dedicated to the invented notes afterwards.

    Args:
        root: The absolute directory to check.

    Raises:
        ConfigurationError: If the root is not an absolute directory, the
            regular files do not match the manifest, the scan finds a symlink
            or special file, a scan limit is exceeded, or an entry cannot be
            inspected.
    """
    try:
        expected = json.loads(
            files("knowledge_server.adapter")
            .joinpath("synthetic-vault.json")
            .read_text(encoding="utf-8")
        )
        if not root.is_absolute() or root.is_symlink() or not root.is_dir():
            raise ValueError
        actual: dict[str, str] = {}
        directories = [root]
        visited = 0
        while directories:
            directory = directories.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > 100 or entry.is_symlink():
                        raise ValueError
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        directories.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        descriptor = os.open(
                            path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                        )
                        with os.fdopen(descriptor, "rb") as source:
                            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                                raise ValueError
                            data = source.read(1048577)
                        if len(data) > 1048576:
                            raise ValueError
                        actual[path.relative_to(root).as_posix()] = hashlib.sha256(
                            data
                        ).hexdigest()
                    else:
                        raise ValueError
        if actual != expected:
            raise ValueError
    except OSError, ValueError:
        raise ConfigurationError("Synthetic HTTP vault verification failed.") from None
