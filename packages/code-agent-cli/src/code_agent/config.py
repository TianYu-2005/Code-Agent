"""Configuration loaded from environment variables."""

import os

from pydantic import SecretStr

from code_agent_llm import OpenAICompatibleConfig

DEFAULT_MODEL = "deepseek-chat"


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


class AppConfig:
    """Runtime configuration assembled from the environment."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None,
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
        trusted_hosts = frozenset({self.base_url_host}) if self.base_url_host else frozenset()
        return OpenAICompatibleConfig(
            api_key=SecretStr(self.api_key),
            base_url=self.base_url,
            trusted_base_url_hosts=trusted_hosts,
        )

    @property
    def base_url_host(self) -> str | None:
        """Extract the hostname from a configured base URL."""
        if not self.base_url:
            return None
        from urllib.parse import urlparse

        return urlparse(self.base_url).hostname


def load_config(workspace: str | None = None) -> AppConfig:
    """Load configuration from environment variables."""
    api_key = os.environ.get("CODE_AGENT_API_KEY", "").strip()
    if not api_key:
        raise ConfigError("CODE_AGENT_API_KEY is not set; export it before starting the agent")
    base_url = os.environ.get("CODE_AGENT_BASE_URL", "").strip() or None
    model = os.environ.get("CODE_AGENT_MODEL", "").strip() or DEFAULT_MODEL
    resolved_workspace = workspace or os.getcwd()
    max_turns = int(os.environ.get("CODE_AGENT_MAX_TURNS", "20"))
    if max_turns < 1:
        raise ConfigError("CODE_AGENT_MAX_TURNS must be a positive integer")
    return AppConfig(
        api_key=api_key,
        base_url=base_url,
        model=model,
        workspace=resolved_workspace,
        max_turns=max_turns,
    )
