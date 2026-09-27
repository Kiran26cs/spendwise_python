"""Domain events recorded by services and dispatched on commit (used for the audit trail).

Concepts: frozen dataclasses, ``kw_only`` fields, ``field(default_factory=...)``,
inheritance, ``ClassVar``, ``@property`` overridden in subclasses, timezone-aware datetimes.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import ClassVar

from spendwise.domain.models import Currency


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, kw_only=True)
class DomainEvent:
    """Base class for everything that happened in the domain."""

    entity_type: ClassVar[str] = "unknown"

    occurred_at: datetime = field(default_factory=_utc_now)

    @property
    def event_type(self) -> str:
        """The event's name, e.g. ``"ExpenseCreated"``."""
        return type(self).__name__

    @property
    def entity_id(self) -> int | None:
        """ID of the affected entity, if the event concerns a single one."""
        return None


@dataclass(frozen=True, kw_only=True)
class CategoryEvent(DomainEvent):
    """Base for events about one category."""

    entity_type: ClassVar[str] = "category"

    category_id: int

    @property
    def entity_id(self) -> int | None:
        return self.category_id


@dataclass(frozen=True, kw_only=True)
class CategoryCreated(CategoryEvent):
    name: str
    monthly_budget: Decimal | None


@dataclass(frozen=True, kw_only=True)
class CategoryUpdated(CategoryEvent):
    name: str
    monthly_budget: Decimal | None


@dataclass(frozen=True, kw_only=True)
class CategoryDeleted(CategoryEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class ExpenseEvent(DomainEvent):
    """Base for events about one expense."""

    entity_type: ClassVar[str] = "expense"

    expense_id: int

    @property
    def entity_id(self) -> int | None:
        return self.expense_id


@dataclass(frozen=True, kw_only=True)
class ExpenseCreated(ExpenseEvent):
    category_id: int
    amount: Decimal
    currency: Currency
    amount_base: Decimal
    spent_on: date


@dataclass(frozen=True, kw_only=True)
class ExpenseUpdated(ExpenseEvent):
    category_id: int
    amount: Decimal
    currency: Currency
    amount_base: Decimal
    spent_on: date


@dataclass(frozen=True, kw_only=True)
class ExpenseDeleted(ExpenseEvent):
    pass


@dataclass(frozen=True, kw_only=True)
class ExpensesImported(DomainEvent):
    """A CSV import finished; ``count`` expenses were created."""

    entity_type: ClassVar[str] = "import"

    count: int
