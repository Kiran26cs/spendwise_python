"""Unit of Work: one transaction boundary that owns the repositories and collected events.

Usage::

    with uow:
        uow.categories.add(category)
        uow.record(CategoryCreated(...))
        uow.commit()          # publishes events, then commits once

Leaving the ``with`` block without ``commit()`` (or via an exception) rolls back.

Concepts: context managers (``__enter__``/``__exit__``), abstract base classes (``abc``),
``Protocol``, ``typing.Self``, inheritance and ``super()``, ``collections.deque``.
"""

from abc import ABC, abstractmethod
from collections import deque
from types import TracebackType
from typing import Protocol, Self

from spendwise.domain.events import DomainEvent
from spendwise.infrastructure.db import SessionFactory
from spendwise.infrastructure.repositories import (
    AuditLogRepository,
    CategoryRepository,
    ExpenseRepository,
    SqlAlchemyAuditLogRepository,
    SqlAlchemyCategoryRepository,
    SqlAlchemyExpenseRepository,
)


class EventPublisher(Protocol):
    """Anything that can dispatch an event to handlers (the ``EventBus`` in Phase 4)."""

    def publish(self, event: DomainEvent, uow: "AbstractUnitOfWork") -> None: ...


class AbstractUnitOfWork(ABC):
    """Transaction boundary shared by the real and fake implementations."""

    categories: CategoryRepository
    expenses: ExpenseRepository
    audit_logs: AuditLogRepository

    def __init__(self, publisher: EventPublisher | None = None) -> None:
        self.publisher = publisher
        self._events: deque[DomainEvent] = deque()

    def __enter__(self) -> Self:
        self._events.clear()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # After a successful commit there is nothing left to roll back, so this is a no-op.
        self.rollback()
        self._events.clear()

    def record(self, event: DomainEvent) -> None:
        """Queue an event to be published when the transaction commits."""
        self._events.append(event)

    def commit(self) -> None:
        """Publish queued events (handlers write in this same transaction), then commit."""
        # Handlers may record further events, so drain the queue rather than iterate it.
        while self._events:
            event = self._events.popleft()
            if self.publisher is not None:
                self.publisher.publish(event, self)
        self._commit()

    @abstractmethod
    def _commit(self) -> None: ...

    @abstractmethod
    def rollback(self) -> None: ...


class SqlAlchemyUnitOfWork(AbstractUnitOfWork):
    """Unit of Work backed by one SQLAlchemy session per ``with`` block."""

    def __init__(
        self, session_factory: SessionFactory, publisher: EventPublisher | None = None
    ) -> None:
        super().__init__(publisher)
        self.session_factory = session_factory

    def __enter__(self) -> Self:
        self.session = self.session_factory()
        self.categories = SqlAlchemyCategoryRepository(self.session)
        self.expenses = SqlAlchemyExpenseRepository(self.session)
        self.audit_logs = SqlAlchemyAuditLogRepository(self.session)
        return super().__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            super().__exit__(exc_type, exc, tb)
        finally:
            self.session.close()

    def _commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
