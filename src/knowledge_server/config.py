"""Explicit configuration for the local knowledge-vault root."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(Exception):
    """A safe startup error for missing or unusable configuration."""


@dataclass(frozen=True, slots=True)
class Config:
    """Server configuration, fixed for the lifetime of the process.

    Attributes:
        root: The vault root, an absolute path with symlinks resolved.
    """

    root: Path


def load_config(environment: Mapping[str, str] | None = None) -> Config:
    """Load and check the vault root from `KNOWLEDGE_ROOT`.

    Args:
        environment: Variables to read instead of the process environment.
            Tests pass a mapping so they never depend on the shell.

    Returns:
        The configuration with the resolved root.

    Raises:
        ConfigurationError: If `KNOWLEDGE_ROOT` is missing or empty, is not an
            absolute path, or does not name an existing directory that the
            process can read and enter.
    """
    source = os.environ if environment is None else environment
    raw_root = source.get("KNOWLEDGE_ROOT")
    if not raw_root:
        raise ConfigurationError(
            "KNOWLEDGE_ROOT must name an absolute readable directory."
        )
    supplied_root = Path(raw_root)
    if not supplied_root.is_absolute():
        raise ConfigurationError(
            "KNOWLEDGE_ROOT must name an absolute readable directory."
        )
    try:
        root = supplied_root.resolve(strict=True)
    except OSError, ValueError:
        raise ConfigurationError(
            "KNOWLEDGE_ROOT must name an existing readable directory."
        ) from None
    try:
        usable_root = root.is_dir() and os.access(root, os.R_OK | os.X_OK)
    except OSError:
        usable_root = False
    if not usable_root:
        raise ConfigurationError(
            "KNOWLEDGE_ROOT must name an existing readable directory."
        )
    return Config(root=root)
