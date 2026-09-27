"""Settings load from environment variables."""

import pytest

from spendwise.core.config import Settings, get_settings


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    monkeypatch.setenv("MAX_UPLOAD_MB", "10")
    monkeypatch.setenv("CORS_ORIGINS", '["http://a.test", "http://b.test"]')

    settings = Settings(_env_file=None)

    assert settings.environment == "prod"
    assert settings.log_level == "WARNING"
    assert settings.max_upload_mb == 10
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.base_currency == "INR"
    assert settings.database_url.startswith("postgresql+psycopg://")


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
