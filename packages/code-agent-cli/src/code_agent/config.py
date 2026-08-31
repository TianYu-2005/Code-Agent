"""Configuration loaded from environment variables."""

import os
from urllib.parse import urlparse

from pydantic import SecretStr

from code_agent_llm import OpenAICompatibleConfig

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


class AppConfig:
    """Runtime configuration assembled from the environment."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        workspace: str,
        max_turns: int,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.workspace = workspace
        self.max_turns = max_turns

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


def load_config(workspace: str | None = None) -> AppConfig:
    """Load configuration from environment variables."""
    api_key = os.environ.get("CODE_AGENT_API_KEY", "").strip()
    if not api_key:
        raise ConfigError("CODE_AGENT_API_KEY is not set; export it before starting the agent")
    base_url = os.environ.get("CODE_AGENT_BASE_URL", "").strip() or DEFAULT_BASE_URL
    model = os.environ.get("CODE_AGENT_MODEL", "").strip() or DEFAULT_MODEL
    resolved_workspace = workspace or os.getcwd()
    max_turns = int(os.environ.get("CODE_AGENT_MAX_TURNS", "30"))
    if max_turns < 1:
        raise ConfigError("CODE_AGENT_MAX_TURNS must be a positive integer")
    return AppConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        workspace=resolved_workspace,
        max_turns=max_turns,
    )
