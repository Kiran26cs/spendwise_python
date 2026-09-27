"""PostgreSQL fixtures: one throwaway container per session, one rolled-back transaction per test.

Concepts: session-scoped fixtures, context managers, SQLAlchemy "join an external transaction"
recipe (``join_transaction_mode="create_savepoint"``).
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine
from sqlalchemy.orm import sessionmaker
from testcontainers.community.postgres import PostgresContainer

from spendwise.api.deps import get_uow
from spendwise.core.config import Settings
from spendwise.infrastructure.db import SessionFactory, create_db_engine
from spendwise.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from spendwise.main import create_app

BACKEND_DIR = Path(__file__).resolve().parents[2]


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    config.attributes["configure_logging"] = False
    return config


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """Start PostgreSQL once and migrate it to the latest revision."""
    with PostgresContainer("postgres:17", driver="psycopg") as postgres:
        url: str = postgres.get_connection_url()
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    db_engine = create_db_engine(database_url)
    yield db_engine
    db_engine.dispose()


@pytest.fixture
def connection(engine: Engine) -> Iterator[Connection]:
    """A connection whose outer transaction is rolled back after each test."""
    with engine.connect() as conn:
        transaction = conn.begin()
        yield conn
        transaction.rollback()


@pytest.fixture
def session_factory(connection: Connection) -> SessionFactory:
    # Session commits/rollbacks become SAVEPOINT operations inside the test's transaction.
    return sessionmaker(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )


@pytest.fixture
def uow(session_factory: SessionFactory) -> SqlAlchemyUnitOfWork:
    return SqlAlchemyUnitOfWork(session_factory)


@pytest.fixture
def api(session_factory: SessionFactory) -> Iterator[TestClient]:
    """The full app (static FX rates) with the Unit of Work bound to the test transaction."""
    app = create_app(Settings(environment="test", _env_file=None))
    event_bus = app.state.event_bus
    app.dependency_overrides[get_uow] = lambda: SqlAlchemyUnitOfWork(
        session_factory, publisher=event_bus
    )
    with TestClient(app) as client:
        yield client
