"""Explicit configuration for the local knowledge-vault root."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class ConfigurationError(Exception):
    """A safe startup error for missing or unusable configuration."""


@dataclass(frozen=True, slots=True)
class Config:
    """Resolved server configuration fixed for the lifetime of the process."""

    root: Path


def load_config(environment: Mapping[str, str] | None = None) -> Config:
    """Load and validate the required absolute root from an environment mapping."""
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
