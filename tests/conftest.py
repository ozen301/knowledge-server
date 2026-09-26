"""Shared pytest configuration."""

import pytest


@pytest.fixture(autouse=True)
def _clear_knowledge_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove KNOWLEDGE_ROOT so no test can reach a vault set in the shell.

    Tests must create synthetic vaults and pass their paths explicitly. This
    guards environment lookups only; it does not isolate the filesystem.
    """
    monkeypatch.delenv("KNOWLEDGE_ROOT", raising=False)
