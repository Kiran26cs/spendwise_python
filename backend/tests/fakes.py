"""In-memory fakes of the repositories and Unit of Work for fast service tests.

Concepts: fakes vs mocks, generics, structural typing (fakes satisfy the Protocols without
inheriting from them), ``copy``, ``dataclasses.replace``.
"""

import copy
from dataclasses import replace
from datetime import date

from spendwise.core.exceptions import NotFoundError
from spendwise.domain.models import AuditLogEntry, Category, Expense
from spendwise.infrastructure.repositories import Entity
from spendwise.infrastructure.unit_of_work import AbstractUnitOfWork, EventPublisher


def _page[T](items: list[T], page: int, page_size: int) -> list[T]:
    start = (page - 1) * page_size
    return items[start : start + page_size]


class FakeRepository[D: Entity]:
    """Dictionary-backed CRUD mirroring ``SqlAlchemyRepository``."""

    def __init__(self) -> None:
        self._items: dict[int, D] = {}
        self._next_id = 1

    def add(self, entity: D) -> D:
        stored = copy.copy(entity)
        stored.id = self._next_id
        self._items[self._next_id] = stored
        self._next_id += 1
        return copy.copy(stored)

    def get(self, entity_id: int) -> D | None:
        item = self._items.get(entity_id)
        return None if item is None else copy.copy(item)

    def update(self, entity: D) -> D:
        if entity.id is None or entity.id not in self._items:
            raise NotFoundError(f"{type(entity).__name__} {entity.id} not found")
        self._items[entity.id] = copy.copy(entity)
        return copy.copy(entity)

    def delete(self, entity_id: int) -> bool:
        return self._items.pop(entity_id, None) is not None


class FakeCategoryRepository(FakeRepository[Category]):
    def get_by_name(self, name: str) -> Category | None:
        wanted = name.strip().lower()
        return next((c for c in self._items.values() if c.name.lower() == wanted), None)

    def list(self) -> list[Category]:
        return sorted(self._items.values(), key=lambda c: c.name)


class FakeExpenseRepository(FakeRepository[Expense]):
    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        date_from: date | None = None,
        date_to: date | None = None,
        category_id: int | None = None,
    ) -> tuple[list[Expense], int]:
        matches = [
            e
            for e in self._items.values()
            if (date_from is None or e.spent_on >= date_from)
            and (date_to is None or e.spent_on <= date_to)
            and (category_id is None or e.category_id == category_id)
        ]
        matches.sort(key=lambda e: (e.spent_on, e.id or 0), reverse=True)
        return _page(matches, page, page_size), len(matches)

    def count_for_category(self, category_id: int) -> int:
        return sum(1 for e in self._items.values() if e.category_id == category_id)


class FakeAuditLogRepository:
    def __init__(self) -> None:
        self.entries: list[AuditLogEntry] = []

    def add(self, entry: AuditLogEntry) -> AuditLogEntry:
        stored = replace(entry, id=len(self.entries) + 1)
        self.entries.append(stored)
        return stored

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        entity_type: str | None = None,
        entity_id: int | None = None,
    ) -> tuple[list[AuditLogEntry], int]:
        matches = [
            e
            for e in reversed(self.entries)
            if (entity_type is None or e.entity_type == entity_type)
            and (entity_id is None or e.entity_id == entity_id)
        ]
        return _page(matches, page, page_size), len(matches)


class FakeUnitOfWork(AbstractUnitOfWork):
    """Unit of Work over the fakes. Data is not transactional; ``committed`` shows intent."""

    def __init__(self, publisher: EventPublisher | None = None) -> None:
        super().__init__(publisher)
        self.categories = FakeCategoryRepository()
        self.expenses = FakeExpenseRepository()
        self.audit_logs = FakeAuditLogRepository()
        self.committed = False
        self.rollbacks = 0

    def _commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rollbacks += 1
