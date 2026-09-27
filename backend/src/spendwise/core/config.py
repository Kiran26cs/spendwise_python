"""Application settings loaded from environment variables and a ``.env`` file (12-factor).

Concepts: pydantic-settings, ``Literal`` types, ``functools.lru_cache`` (cached factory).
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from spendwise import __version__


class Settings(BaseSettings):
    """All runtime configuration. Field names map to upper-case environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "SpendWise"
    app_version: str = __version__
    environment: Literal["local", "test", "prod"] = "local"
    debug: bool = False
    database_url: str = "postgresql+psycopg://spendwise:spendwise@localhost:5433/spendwise"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = True
    base_currency: Literal["INR"] = "INR"  # fixed: amounts are stored converted to INR
    fx_api_url: str = "https://api.frankfurter.dev/v1"
    fx_timeout_seconds: float = 5.0
    fx_cache_ttl_seconds: int = 3600
    cors_origins: list[str] = ["http://localhost:4200"]
    max_upload_mb: int = 5


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, created once on first call."""
    return Settings()
