"""Checks that tests do not inherit knowledge-root configuration from the shell."""

import os


def test_knowledge_root_is_not_inherited() -> None:
    """A developer's KNOWLEDGE_ROOT never reaches test code."""
    assert "KNOWLEDGE_ROOT" not in os.environ
