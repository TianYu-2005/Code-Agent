"""Tests for TOML-based configuration, profiles, and the approval mode."""

from pathlib import Path

import pytest

from code_agent.config import (
    ApprovalMode,
    ConfigError,
    load_config,
    save_config_file,
)


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point CODE_AGENT_HOME at an empty temp directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CODE_AGENT_HOME", str(home))
    return home


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CODE_AGENT_API_KEY",
        "CODE_AGENT_BASE_URL",
        "CODE_AGENT_MODEL",
        "CODE_AGENT_MAX_TURNS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_global_config_file_supplies_key(
    isolated_home: Path,
    clean_env: None,
    tmp_path: Path,
) -> None:
    save_config_file(
        isolated_home / "config.toml",
        {"api_key": "file-key", "model": "deepseek-v4"},
    )

    config = load_config(workspace=str(tmp_path))

    assert config.api_key == "file-key"
    assert config.model == "deepseek-v4"
    assert config.base_url == "https://api.deepseek.com"
    assert config.approval_mode is ApprovalMode.ASK


def test_project_config_overrides_global(
    isolated_home: Path,
    clean_env: None,
    tmp_path: Path,
) -> None:
    save_config_file(isolated_home / "config.toml", {"api_key": "global-key", "model": "a"})
    project_dir = tmp_path / ".code-agent"
    project_dir.mkdir()
    (project_dir / "config.toml").write_text('model = "b"\n', encoding="utf-8")

    config = load_config(workspace=str(tmp_path))

    assert config.api_key == "global-key"
    assert config.model == "b"


def test_env_overrides_files(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    save_config_file(isolated_home / "config.toml", {"api_key": "file-key"})
    monkeypatch.setenv("CODE_AGENT_API_KEY", "env-key")
    monkeypatch.setenv("CODE_AGENT_MODEL", "env-model")

    config = load_config(workspace=str(tmp_path))

    assert config.api_key == "env-key"
    assert config.model == "env-model"


def test_cli_overrides_win(
    isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    save_config_file(isolated_home / "config.toml", {"api_key": "file-key"})
    monkeypatch.setenv("CODE_AGENT_API_KEY", "env-key")

    config = load_config(workspace=str(tmp_path), overrides={"model": "cli-model"})

    assert config.model == "cli-model"
    assert config.api_key == "env-key"  # overrides only carry provided keys


def test_profiles_parsed_and_merged_with_builtins(
    isolated_home: Path,
    clean_env: None,
    tmp_path: Path,
) -> None:
    save_config_file(
        isolated_home / "config.toml",
        {
            "api_key": "file-key",
            "profiles": {
                "local-ollama": {
                    "base_url": "http://localhost:11434/v1",
                    "model": "qwen2.5-coder:32b",
                    "api_key": "ollama",
                }
            },
        },
    )

    config = load_config(workspace=str(tmp_path))

    profiles = config.available_profiles()
    assert "deepseek-v4" in profiles  # builtin preset
    assert profiles["local-ollama"].model == "qwen2.5-coder:32b"
    assert profiles["local-ollama"].api_key == "ollama"

    # apply_profile switches model/endpoint/key together
    config.apply_profile(profiles["local-ollama"])
    assert config.model == "qwen2.5-coder:32b"
    assert config.base_url == "http://localhost:11434/v1"
    assert config.api_key == "ollama"


def test_approval_mode_from_config(
    isolated_home: Path,
    clean_env: None,
    tmp_path: Path,
) -> None:
    save_config_file(isolated_home / "config.toml", {"api_key": "k", "approval_mode": "auto"})

    config = load_config(workspace=str(tmp_path))

    assert config.approval_mode is ApprovalMode.AUTO


def test_invalid_approval_mode_rejected(
    isolated_home: Path,
    clean_env: None,
    tmp_path: Path,
) -> None:
    save_config_file(isolated_home / "config.toml", {"api_key": "k", "approval_mode": "yolo"})

    with pytest.raises(ConfigError):
        load_config(workspace=str(tmp_path))


def test_save_config_file_restricts_permissions(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "config.toml"

    save_config_file(path, {"api_key": "secret", "model": ""})

    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    content = path.read_text(encoding="utf-8")
    assert "secret" in content
    assert "model" not in content  # empty values are dropped


def test_missing_key_reports_config_error(
    isolated_home: Path,
    clean_env: None,
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(workspace=str(tmp_path))
    assert "API key" in str(excinfo.value)
