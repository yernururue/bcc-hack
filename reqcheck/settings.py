"""Настройки из переменных окружения и `.env` (см. docs/02-trd.md, §2)."""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Самая дешёвая модель со structured output на OpenRouter (2026-11-25). Для разработки и тестов.
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("LLM_API_KEY", "OPENROUTER_API_KEY")
    )
    llm_base_url: str = Field(default=OPENROUTER_BASE_URL, validation_alias="LLM_BASE_URL")
    llm_model: str = Field(default=DEFAULT_MODEL, validation_alias="LLM_MODEL")
    llm_max_cost_usd: float = Field(default=0.50, gt=0, validation_alias="LLM_MAX_COST_USD")
    cache_dir: str = Field(default=".cache/reqcheck", validation_alias="REQCHECK_CACHE_DIR")
    log_level: str = Field(default="INFO", validation_alias="REQCHECK_LOG_LEVEL")
    api_token: SecretStr | None = Field(default=None, validation_alias="REQCHECK_API_TOKEN")

    @property
    def is_openrouter(self) -> bool:
        return "openrouter.ai" in self.llm_base_url

    @property
    def cache_path(self) -> Path | None:
        """Каталог кэша LLM или None, если кэш выключен (`REQCHECK_CACHE_DIR=off`)."""
        if self.cache_dir.strip().lower() in {"", "off", "none", "0"}:
            return None
        return Path(self.cache_dir)


@lru_cache
def get_settings() -> Settings:
    return Settings()
