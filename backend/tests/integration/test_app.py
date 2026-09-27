"""Health endpoint, correlation ID header and Problem Details error responses."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from spendwise.api.deps import get_uow
from spendwise.api.routers import health
from spendwise.core.config import Settings
from spendwise.core.exceptions import ConflictError
from spendwise.infrastructure.fx_client import FrankfurterFxProvider
from spendwise.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from spendwise.main import create_app


@pytest.fixture(autouse=True)
def database_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests don't need PostgreSQL; the real ping is covered in test_health_db.py."""
    monkeypatch.setattr(health, "ping", lambda engine: True)


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "0.1.0", "database": "ok"}


def test_health_degraded_when_database_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health, "ping", lambda engine: False)

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "version": "0.1.0", "database": "unavailable"}


def test_correlation_id_generated(client: TestClient) -> None:
    response = client.get("/health")

    assert len(response.headers["X-Correlation-ID"]) == 36  # UUID4


def test_correlation_id_echoed(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Correlation-ID": "abc-123"})

    assert response.headers["X-Correlation-ID"] == "abc-123"


def test_unknown_route_returns_problem_details(client: TestClient) -> None:
    response = client.get("/nope", headers={"X-Correlation-ID": "cid-404"})

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["instance"] == "/nope"
    assert body["correlation_id"] == "cid-404"


def test_app_error_maps_to_status(app: FastAPI, client: TestClient) -> None:
    @app.get("/boom-conflict")
    def conflict() -> None:
        raise ConflictError("Category 'Food' already exists")

    response = client.get("/boom-conflict")

    assert response.status_code == 409
    assert response.json()["detail"] == "Category 'Food' already exists"


def test_validation_error_includes_errors(app: FastAPI, client: TestClient) -> None:
    @app.get("/needs-int")
    def needs_int(n: int) -> int:
        return n

    response = client.get("/needs-int", params={"n": "abc"})

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["errors"][0]["loc"] == ["query", "n"]


def test_unhandled_error_returns_500_without_stack_trace(app: FastAPI, client: TestClient) -> None:
    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    response = client.get("/boom", headers={"X-Correlation-ID": "cid-500"})

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["X-Correlation-ID"] == "cid-500"
    body = response.json()
    assert body["correlation_id"] == "cid-500"
    assert "secret" not in response.text
    assert "Traceback" not in response.text


def test_production_wiring() -> None:
    """Outside tests the app uses live FX rates and a SQLAlchemy Unit of Work on the event bus."""
    app = create_app(Settings(environment="local", _env_file=None))
    with TestClient(app):
        assert isinstance(app.state.fx_provider, FrankfurterFxProvider)
        uow = get_uow(Request({"type": "http", "app": app}))
    assert isinstance(uow, SqlAlchemyUnitOfWork)
    assert uow.publisher is app.state.event_bus


def test_openapi_docs_available(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").json()["info"]["title"] == "SpendWise"
