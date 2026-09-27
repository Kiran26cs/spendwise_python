"""Health endpoint against a real database."""

from fastapi.testclient import TestClient

from spendwise.core.config import Settings
from spendwise.main import create_app


def test_health_reports_database_ok(database_url: str) -> None:
    settings = Settings(environment="test", database_url=database_url, _env_file=None)
    with TestClient(create_app(settings)) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0", "database": "ok"}
