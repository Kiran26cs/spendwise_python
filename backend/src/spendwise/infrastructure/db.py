"""SQLAlchemy engine/session setup and ORM table models.

Concepts: SQLAlchemy 2.0 typed declarative mapping (``Mapped[]``, ``mapped_column``),
``@contextmanager``, generators, ``try/except/else/finally``, type aliases.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Engine,
    ForeignKey,
    Numeric,
    String,
    create_engine,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

logger = logging.getLogger(__name__)

type SessionFactory = sessionmaker[Session]

MONEY = Numeric(12, 2, asdecimal=True)
FX_RATE = Numeric(18, 6, asdecimal=True)


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class TimestampMixin:
    """``created_at`` / ``updated_at`` columns maintained by the ORM (UTC)."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class CategoryRow(TimestampMixin, Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True)
    monthly_budget: Mapped[Decimal | None] = mapped_column(MONEY)


class ExpenseRow(TimestampMixin, Base):
    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(String(3))
    amount_base: Mapped[Decimal] = mapped_column(MONEY)
    fx_rate: Mapped[Decimal] = mapped_column(FX_RATE)
    description: Mapped[str] = mapped_column(String(200))
    spent_on: Mapped[date] = mapped_column(index=True)


class AuditLogRow(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int | None]
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    correlation_id: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def create_db_engine(database_url: str) -> Engine:
    """Create the engine. No connection is opened until first use."""
    return create_engine(database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})


def create_session_factory(engine: Engine) -> SessionFactory:
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: SessionFactory) -> Iterator[Session]:
    """Provide a session that commits on success and rolls back on error (for scripts)."""
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    else:
        session.commit()
    finally:
        session.close()


def ping(engine: Engine) -> bool:
    """Return True if the database answers ``SELECT 1``."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("Database ping failed", exc_info=True)
        return False
    return True
