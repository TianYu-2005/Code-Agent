"""Configuration assembled from TOML files, environment variables, and CLI overrides.

Precedence (highest wins):
    CLI overrides  >  environment variables  >  project config  >  global config  >  defaults

Global config lives at ``~/.code-agent/config.toml`` (override the directory with
``CODE_AGENT_HOME``); project config at ``<workspace>/.code-agent/config.toml``.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import tomli_w
from pydantic import SecretStr

from code_agent_llm import OpenAICompatibleConfig

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MAX_TURNS = 30

ENV_API_KEY = "CODE_AGENT_API_KEY"
ENV_BASE_URL = "CODE_AGENT_BASE_URL"
ENV_MODEL = "CODE_AGENT_MODEL"
ENV_MAX_TURNS = "CODE_AGENT_MAX_TURNS"
ENV_HOME = "CODE_AGENT_HOME"

CONFIG_DIR = ".code-agent"
CONFIG_FILE = "config.toml"


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


class ApprovalMode(StrEnum):
    """Whether tool executions need interactive confirmation."""

    ASK = "ask"
    AUTO = "auto"


@dataclass(frozen=True)
class ModelProfile:
    """A named combination of model, endpoint, and optional API key."""

    name: str
    model: str
    base_url: str | None = None
    api_key: str | None = None


BUILTIN_PROFILES: dict[str, ModelProfile] = {
    profile.name: profile
    for profile in (
        ModelProfile("deepseek-v4-flash", "deepseek-v4-flash", DEEPSEEK_BASE_URL),
        ModelProfile("deepseek-v4", "deepseek-v4", DEEPSEEK_BASE_URL),
        ModelProfile("deepseek-reasoner", "deepseek-reasoner", DEEPSEEK_BASE_URL),
    )
}


class AppConfig:
    """Runtime configuration assembled from every source."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        workspace: str,
        max_turns: int,
        approval_mode: ApprovalMode = ApprovalMode.ASK,
        profiles: dict[str, ModelProfile] | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.workspace = workspace
        self.max_turns = max_turns
        self.approval_mode = approval_mode
        self.profiles: dict[str, ModelProfile] = dict(profiles or {})

    @property
    def provider_config(self) -> OpenAICompatibleConfig:
        """Build the OpenAI-compatible provider connection settings."""
        return OpenAICompatibleConfig(
            api_key=SecretStr(self.api_key),
            base_url=self.base_url,
            trusted_base_url_hosts=frozenset({self.base_url_host}),
        )

    @property
    def base_url_host(self) -> str:
        """Extract the hostname from the configured base URL."""
        return urlparse(self.base_url).hostname or ""

    def available_profiles(self) -> dict[str, ModelProfile]:
        """Builtin presets plus profiles declared in config files."""
        return {**BUILTIN_PROFILES, **self.profiles}

    def resolve_profile(self, name: str) -> ModelProfile | None:
        """Look up a profile by name, or accept a bare model name."""
        profiles = self.available_profiles()
        if name in profiles:
            return profiles[name]
        return None

    def apply_profile(self, profile: ModelProfile) -> None:
        """Adopt a profile's settings, keeping current values for omitted fields."""
        self.model = profile.model
        if profile.base_url:
            self.base_url = profile.base_url
        if profile.api_key:
            self.api_key = profile.api_key


def global_config_dir() -> Path:
    """Directory holding the user-level configuration."""
    home = os.environ.get(ENV_HOME, "").strip() or str(Path.home() / CONFIG_DIR)
    return Path(home).expanduser()


def global_config_path() -> Path:
    """Path of the user-level configuration file."""
    return global_config_dir() / CONFIG_FILE


def project_config_path(workspace: str | Path) -> Path:
    """Path of the workspace-level configuration file."""
    return Path(workspace) / CONFIG_DIR / CONFIG_FILE


def _read_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise ConfigError(f"cannot read config file {path}: {error}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in {path}: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"invalid TOML in {path}: expected a table")
    return data


def _parse_profiles(raw: object, source: str) -> dict[str, ModelProfile]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigError(f"invalid [profiles] table in {source}")
    profiles: dict[str, ModelProfile] = {}
    for name, body in raw.items():
        if not isinstance(body, dict) or not isinstance(body.get("model"), str):
            raise ConfigError(f"profile {name!r} in {source} needs a string 'model'")
        base_url = body.get("base_url")
        api_key = body.get("api_key")
        if base_url is not None and not isinstance(base_url, str):
            raise ConfigError(f"profile {name!r} in {source} has an invalid 'base_url'")
        if api_key is not None and not isinstance(api_key, str):
            raise ConfigError(f"profile {name!r} in {source} has an invalid 'api_key'")
        profiles[name] = ModelProfile(
            name=name,
            model=body["model"],
            base_url=base_url,
            api_key=api_key,
        )
    return profiles


def _parse_approval_mode(raw: object) -> ApprovalMode:
    if raw is None:
        return ApprovalMode.ASK
    try:
        return ApprovalMode(str(raw))
    except ValueError as error:
        raise ConfigError(f"invalid approval_mode {raw!r}; expected 'ask' or 'auto'") from error


def load_config(
    workspace: str | None = None,
    overrides: dict[str, str] | None = None,
) -> AppConfig:
    """Load configuration from files, environment, and CLI overrides."""
    resolved_workspace = str(workspace or os.getcwd())

    global_values = _read_toml(global_config_path())
    project_values = _read_toml(project_config_path(resolved_workspace))
    values: dict[str, object] = {**global_values, **project_values}

    for env_name, key in (
        (ENV_API_KEY, "api_key"),
        (ENV_BASE_URL, "base_url"),
        (ENV_MODEL, "model"),
    ):
        env_value = os.environ.get(env_name, "").strip()
        if env_value:
            values[key] = env_value

    for key, value in (overrides or {}).items():
        if value:
            values[key] = value

    api_key = values.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise ConfigError(
            "API key is not configured; set CODE_AGENT_API_KEY, run the first-start "
            f"wizard, or add api_key to {global_config_path()}"
        )
    base_url = values.get("base_url") or DEFAULT_BASE_URL
    if not isinstance(base_url, str):
        raise ConfigError("base_url must be a string")
    model = values.get("model") or DEFAULT_MODEL
    if not isinstance(model, str):
        raise ConfigError("model must be a string")

    raw_turns = os.environ.get(ENV_MAX_TURNS, "").strip()
    if raw_turns:
        max_turns = int(raw_turns)
    else:
        raw_value = values.get("max_turns", DEFAULT_MAX_TURNS)
        if not isinstance(raw_value, int) or isinstance(raw_value, bool):
            raise ConfigError("max_turns must be an integer")
        max_turns = raw_value
    if max_turns < 1:
        raise ConfigError("max_turns must be a positive integer")

    profiles = _parse_profiles(values.get("profiles"), "config file")
    mode = _parse_approval_mode(values.get("approval_mode"))

    return AppConfig(
        api_key=api_key.strip(),
        base_url=base_url,
        model=model,
        workspace=resolved_workspace,
        max_turns=max_turns,
        approval_mode=mode,
        profiles=profiles,
    )


def save_config_file(path: Path, values: dict[str, Any]) -> Path:
    """Write a TOML config file with user-only permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: value for key, value in values.items() if value}
    path.write_bytes(tomli_w.dumps(payload).encode("utf-8"))
    try:
        path.chmod(0o600)
    except OSError:
        pass  # non-POSIX filesystems may not support chmod
    return path


def first_run_wizard(path: Path) -> dict[str, str]:
    """Interactively collect connection settings and save them to ``path``."""
    import sys

    out = sys.stdout
    out.write("未检测到 API Key，开始首次配置（保存到 " + str(path) + "）\n")
    api_key = ""
    while not api_key:
        api_key = input("API Key (例如 sk-...): ").strip()
        if not api_key:
            out.write("API Key 不能为空。\n")
    base_url = input(f"Base URL [{DEFAULT_BASE_URL}]: ").strip() or DEFAULT_BASE_URL
    model = input(f"Model [{DEFAULT_MODEL}]: ").strip() or DEFAULT_MODEL
    values = {"api_key": api_key, "base_url": base_url, "model": model}
    save_config_file(path, values)
    out.write(f"配置已保存到 {path}\n")
    return values


def load_config_or_wizard(
    workspace: str | None = None,
    overrides: dict[str, str] | None = None,
) -> AppConfig:
    """Load config; on a missing API key in an interactive terminal, run the wizard."""
    import sys

    try:
        return load_config(workspace=workspace, overrides=overrides)
    except ConfigError as error:
        if "API key" not in str(error) or not sys.stdin.isatty():
            raise
    first_run_wizard(global_config_path())
    return load_config(workspace=workspace, overrides=overrides)
