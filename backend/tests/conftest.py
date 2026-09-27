"""Shared pytest fixtures.

Concepts: pytest fixtures, fixture composition, generators with ``yield`` for teardown.
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from spendwise.core.config import Settings
from spendwise.main import create_app


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test", log_json=True, log_level="DEBUG", _env_file=None)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
