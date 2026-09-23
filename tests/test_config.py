"""Tests for explicit, safely validated server configuration."""

from pathlib import Path

import pytest

from knowledge_server.config import ConfigurationError, load_config


def test_root_must_be_explicit_absolute_readable_directory(tmp_path: Path) -> None:
    """Startup does not infer a root from its working directory."""
    with pytest.raises(ConfigurationError) as missing:
        load_config({})
    assert str(tmp_path) not in str(missing.value)

    with pytest.raises(ConfigurationError):
        load_config({"KNOWLEDGE_ROOT": "relative"})
    with pytest.raises(ConfigurationError):
        load_config({"KNOWLEDGE_ROOT": "\x00not-a-path"})
    with pytest.raises(ConfigurationError):
        load_config({"KNOWLEDGE_ROOT": f"{tmp_path}\x00not-a-path"})
    with pytest.raises(ConfigurationError):
        load_config({"KNOWLEDGE_ROOT": ""})
    with pytest.raises(ConfigurationError):
        load_config({"KNOWLEDGE_ROOT": str(tmp_path / "missing")})
    root_file = tmp_path / "root-file"
    root_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config({"KNOWLEDGE_ROOT": str(root_file)})

    config = load_config({"KNOWLEDGE_ROOT": str(tmp_path)})
    assert config.root == tmp_path.resolve()


def test_root_access_failure_has_no_filesystem_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Startup uses a testable read/search check even for privileged runners."""
    monkeypatch.setattr("knowledge_server.config.os.access", lambda *_: False)
    with pytest.raises(ConfigurationError) as error:
        load_config({"KNOWLEDGE_ROOT": str(tmp_path)})
    assert str(tmp_path) not in str(error.value)


def test_root_metadata_failure_is_a_safe_configuration_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Root metadata errors never escape startup with their OS detail."""

    def deny_directory(_: Path) -> bool:
        raise PermissionError(f"private {tmp_path}")

    monkeypatch.setattr(Path, "is_dir", deny_directory)
    with pytest.raises(ConfigurationError) as error:
        load_config({"KNOWLEDGE_ROOT": str(tmp_path)})
    assert "private" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


def test_root_symlink_resolves_at_startup(tmp_path: Path) -> None:
    """A controlled root symlink is resolved once before request handling."""
    link = tmp_path.parent / f"{tmp_path.name}-link"
    link.symlink_to(tmp_path, target_is_directory=True)
    assert load_config({"KNOWLEDGE_ROOT": str(link)}).root == tmp_path.resolve()
