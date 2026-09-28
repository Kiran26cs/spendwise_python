"""Repository pattern: collection-like access to aggregates; all SQL lives here.

Services depend on the ``Protocol`` interfaces; ``SqlAlchemy*`` classes implement them and
tests use in-memory fakes (``tests/fakes.py``).

Concepts: ``typing.Protocol`` (structural typing), generics with PEP 695 syntax
(``class Repo[D, R]``), abstract methods, keyword-only args, tuples as return values,
generators (``yield``) for streaming results, SQLAlchemy 2.0 ``select()`` queries.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from datetime import date
from typing import Protocol

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from spendwise.core.exceptions import NotFoundError
from spendwise.domain.models import AuditLogEntry, Category, Expense
from spendwise.infrastructure import mappers
from spendwise.infrastructure.db import AuditLogRow, Base, CategoryRow, ExpenseRow

# --- Interfaces (what services see) ------------------------------------------


class Entity(Protocol):
    id: int | None


class CategoryRepository(Protocol):
    def add(self, category: Category) -> Category: ...
    def get(self, entity_id: int) -> Category | None: ...
    def update(self, category: Category) -> Category: ...
    def delete(self, entity_id: int) -> bool: ...
    def get_by_name(self, name: str) -> Category | None: ...
    def list(self) -> list[Category]: ...


class ExpenseRepository(Protocol):
    def add(self, expense: Expense) -> Expense: ...
    def get(self, entity_id: int) -> Expense | None: ...
    def update(self, expense: Expense) -> Expense: ...
    def delete(self, entity_id: int) -> bool: ...
    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        date_from: date | None = None,
        date_to: date | None = None,
        category_id: int | None = None,
    ) -> tuple[list[Expense], int]: ...
    def count_for_category(self, category_id: int) -> int: ...
    def add_many(self, expenses: Iterable[Expense]) -> int: ...
    def iter_between(self, date_from: date, date_to: date) -> Iterator[Expense]: ...


class AuditLogRepository(Protocol):
    def add(self, entry: AuditLogEntry) -> AuditLogEntry: ...
    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        entity_type: str | None = None,
        entity_id: int | None = None,
    ) -> tuple[list[AuditLogEntry], int]: ...


# --- SQLAlchemy implementations ----------------------------------------------


def _offset(page: int, page_size: int) -> int:
    """Row offset for 1-based ``page``."""
    if page < 1 or page_size < 1:
        raise ValueError("page and page_size must be >= 1")
    return (page - 1) * page_size


class SqlAlchemyRepository[D: Entity, R: Base](ABC):
    """Generic CRUD for domain type ``D`` stored as ORM row type ``R``."""

    row_type: type[R]
    entity_name: str

    def __init__(self, session: Session) -> None:
        self.session = session

    @abstractmethod
    def _to_domain(self, row: R) -> D: ...

    @abstractmethod
    def _to_row(self, entity: D) -> R: ...

    @abstractmethod
    def _update_row(self, row: R, entity: D) -> None: ...

    def add(self, entity: D) -> D:
        """Insert ``entity`` and return it with its generated ID."""
        row = self._to_row(entity)
        self.session.add(row)
        self.session.flush()
        return self._to_domain(row)

    def get(self, entity_id: int) -> D | None:
        row = self.session.get(self.row_type, entity_id)
        return None if row is None else self._to_domain(row)

    def update(self, entity: D) -> D:
        """Write all fields of ``entity`` to its existing row."""
        if entity.id is None:
            raise ValueError("Cannot update an entity without an id")
        row = self.session.get(self.row_type, entity.id)
        if row is None:
            raise NotFoundError(f"{self.entity_name} {entity.id} not found")
        self._update_row(row, entity)
        self.session.flush()
        return self._to_domain(row)

    def delete(self, entity_id: int) -> bool:
        """Delete the row; return False if it did not exist."""
        row = self.session.get(self.row_type, entity_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True


class SqlAlchemyCategoryRepository(SqlAlchemyRepository[Category, CategoryRow]):
    row_type = CategoryRow
    entity_name = "Category"

    def _to_domain(self, row: CategoryRow) -> Category:
        return mappers.category_to_domain(row)

    def _to_row(self, entity: Category) -> CategoryRow:
        return mappers.category_to_row(entity)

    def _update_row(self, row: CategoryRow, entity: Category) -> None:
        mappers.update_category_row(row, entity)

    def get_by_name(self, name: str) -> Category | None:
        """Case-insensitive lookup by name."""
        stmt = select(CategoryRow).where(func.lower(CategoryRow.name) == name.strip().lower())
        row = self.session.scalars(stmt).first()
        return None if row is None else self._to_domain(row)

    def list(self) -> list[Category]:
        rows = self.session.scalars(select(CategoryRow).order_by(CategoryRow.name))
        return [self._to_domain(row) for row in rows]


class SqlAlchemyExpenseRepository(SqlAlchemyRepository[Expense, ExpenseRow]):
    row_type = ExpenseRow
    entity_name = "Expense"

    def _to_domain(self, row: ExpenseRow) -> Expense:
        return mappers.expense_to_domain(row)

    def _to_row(self, entity: Expense) -> ExpenseRow:
        return mappers.expense_to_row(entity)

    def _update_row(self, row: ExpenseRow, entity: Expense) -> None:
        mappers.update_expense_row(row, entity)

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        date_from: date | None = None,
        date_to: date | None = None,
        category_id: int | None = None,
    ) -> tuple[list[Expense], int]:
        """Return one page of expenses (newest ``spent_on`` first) and the total match count."""
        conditions: list[ColumnElement[bool]] = []
        if date_from is not None:
            conditions.append(ExpenseRow.spent_on >= date_from)
        if date_to is not None:
            conditions.append(ExpenseRow.spent_on <= date_to)
        if category_id is not None:
            conditions.append(ExpenseRow.category_id == category_id)

        total = self.session.scalar(select(func.count()).select_from(ExpenseRow).where(*conditions))
        stmt = (
            select(ExpenseRow)
            .where(*conditions)
            .order_by(ExpenseRow.spent_on.desc(), ExpenseRow.id.desc())
            .offset(_offset(page, page_size))
            .limit(page_size)
        )
        return [self._to_domain(row) for row in self.session.scalars(stmt)], total or 0

    def count_for_category(self, category_id: int) -> int:
        stmt = select(func.count()).where(ExpenseRow.category_id == category_id)
        return self.session.scalar(stmt) or 0

    def add_many(self, expenses: Iterable[Expense]) -> int:
        """Insert many expenses with a single flush; return how many were added."""
        rows = [self._to_row(expense) for expense in expenses]
        self.session.add_all(rows)
        self.session.flush()
        return len(rows)

    def iter_between(self, date_from: date, date_to: date) -> Iterator[Expense]:
        """Stream expenses in ``[date_from, date_to]`` ordered by category (for grouping).

        A generator: rows are fetched from the database in chunks as the caller iterates.
        """
        stmt = (
            select(ExpenseRow)
            .where(ExpenseRow.spent_on.between(date_from, date_to))
            .order_by(ExpenseRow.category_id, ExpenseRow.spent_on, ExpenseRow.id)
            .execution_options(yield_per=500)
        )
        for row in self.session.scalars(stmt):
            yield self._to_domain(row)


class SqlAlchemyAuditLogRepository:
    """Append-only audit trail (no update/delete, so it does not use the CRUD base)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, entry: AuditLogEntry) -> AuditLogEntry:
        row = mappers.audit_log_to_row(entry)
        self.session.add(row)
        self.session.flush()
        return mappers.audit_log_to_domain(row)

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        entity_type: str | None = None,
        entity_id: int | None = None,
    ) -> tuple[list[AuditLogEntry], int]:
        """Return one page of entries (newest first) and the total match count."""
        conditions: list[ColumnElement[bool]] = []
        if entity_type is not None:
            conditions.append(AuditLogRow.entity_type == entity_type)
        if entity_id is not None:
            conditions.append(AuditLogRow.entity_id == entity_id)

        total = self.session.scalar(
            select(func.count()).select_from(AuditLogRow).where(*conditions)
        )
        stmt = (
            select(AuditLogRow)
            .where(*conditions)
            .order_by(AuditLogRow.occurred_at.desc(), AuditLogRow.id.desc())
            .offset(_offset(page, page_size))
            .limit(page_size)
        )
        rows = self.session.scalars(stmt)
        return [mappers.audit_log_to_domain(row) for row in rows], total or 0
