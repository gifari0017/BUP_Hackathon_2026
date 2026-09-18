"""Runtime configuration.

Only environment-variable *names* appear in this file. Values are read from the environment at
startup and never logged, echoed in a response, or written to disk.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Service settings, all overridable by environment variable."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- primary language model -------------------------------------------------
    gridwise_llm_provider: str = "gemini"
    gridwise_llm_model: str = "gemini-3.5-flash-lite"

    # --- optional second language-model provider (failover) ---------------------
    # Never a non-LLM interpreter: the mandatory-LLM rule applies to every success path.
    gridwise_llm_fallback_provider: str | None = None
    gridwise_llm_fallback_model: str | None = None

    # --- credentials ------------------------------------------------------------
    gemini_api_key: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None

    # --- request handling -------------------------------------------------------
    llm_timeout_seconds: float = 12.0
    llm_transport_retries: int = 3
    llm_repair_attempts: int = 1

    # --- server -----------------------------------------------------------------
    port: int = 8000
    log_level: str = "INFO"

    def api_key_for(self, provider: str) -> str | None:
        """Return the configured credential for a provider, or None when unset."""
        return {
            "gemini": self.gemini_api_key,
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
        }.get(provider)

    def configured_providers(self) -> list[tuple[str, str]]:
        """Provider/model pairs to try in order. Primary first, failover second."""
        chain: list[tuple[str, str]] = [(self.gridwise_llm_provider, self.gridwise_llm_model)]
        if self.gridwise_llm_fallback_provider:
            chain.append(
                (
                    self.gridwise_llm_fallback_provider,
                    self.gridwise_llm_fallback_model or self.gridwise_llm_model,
                )
            )
        return chain


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """Test hook: force the next get_settings() call to re-read the environment."""
    global _settings
    _settings = None
