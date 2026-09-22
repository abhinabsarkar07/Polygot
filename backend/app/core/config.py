"""Centralized application configuration.

Every setting the app needs comes from here, and this module is the only
place that reads environment variables. Nothing else should call
``os.environ`` directly -- that keeps config discoverable in one file and
makes it possible to override everything in tests via ``Settings(...)``.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str
    frontend_origin: str = "http://localhost:5173"

    # Reserved for CP-03+ (provider adapters). Declared here now so
    # .env.example is complete and stable, but nothing in CP-01 reads them.
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    openai_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Settings are read once per process and cached -- env vars don't
    change at runtime, so re-parsing them on every request would be waste."""
    return Settings()
