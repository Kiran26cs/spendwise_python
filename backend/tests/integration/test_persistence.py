"""Repositories, Unit of Work and migrations against a real PostgreSQL database."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Connection, Engine, select
from sqlalchemy.exc import IntegrityError

from spendwise.core.exceptions import NotFoundError
from spendwise.domain.events import DomainEvent, ExpensesImported
from spendwise.domain.models import AuditLogEntry, Category, Currency, Expense
from spendwise.infrastructure.db import (
    Base,
    CategoryRow,
    SessionFactory,
    create_db_engine,
    ping,
    session_scope,
)
from spendwise.infrastructure.unit_of_work import AbstractUnitOfWork, SqlAlchemyUnitOfWork


def new_expense(category_id: int, **overrides: object) -> Expense:
    fields: dict[str, object] = {
        "category_id": category_id,
        "amount": Decimal("100.00"),
        "currency": Currency.INR,
        "fx_rate": Decimal("1"),
        "description": "test",
        "spent_on": date(2026, 9, 1),
    }
    fields.update(overrides)
    return Expense.create(**fields)  # type: ignore[arg-type]


def add_category(uow: SqlAlchemyUnitOfWork, name: str = "Food") -> Category:
    with uow:
        category = uow.categories.add(Category(id=None, name=name))
        uow.commit()
    return category


# --- Migrations ---------------------------------------------------------------


def test_migrations_match_orm_models(connection: Connection) -> None:
    diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == []


# --- Categories ---------------------------------------------------------------


def test_category_crud(uow: SqlAlchemyUnitOfWork) -> None:
    with uow:
        created = uow.categories.add(
            Category(id=None, name="Groceries", monthly_budget=Decimal("5000.00"))
        )
        uow.commit()
    assert created.id is not None

    with uow:
        assert uow.categories.get(created.id) == created
        assert uow.categories.get_by_name("  groceries ") == created
        updated = uow.categories.update(Category(id=created.id, name="Food", monthly_budget=None))
        uow.commit()
    assert updated.name == "Food"
    assert updated.monthly_budget is None

    with uow:
        assert uow.categories.delete(created.id) is True
        assert uow.categories.delete(created.id) is False
        uow.commit()
    with uow:
        assert uow.categories.get(created.id) is None


def test_category_list_sorted_by_name(uow: SqlAlchemyUnitOfWork) -> None:
    for name in ["Travel", "Bills", "Food"]:
        add_category(uow, name)
    with uow:
        assert [c.name for c in uow.categories.list()] == ["Bills", "Food", "Travel"]


def test_duplicate_category_name_rejected(uow: SqlAlchemyUnitOfWork) -> None:
    add_category(uow, "Food")
    with uow, pytest.raises(IntegrityError):
        uow.categories.add(Category(id=None, name="Food"))


def test_update_missing_entity_raises(uow: SqlAlchemyUnitOfWork) -> None:
    with uow, pytest.raises(NotFoundError, match="Category 999 not found"):
        uow.categories.update(Category(id=999, name="Ghost"))


def test_update_without_id_raises(uow: SqlAlchemyUnitOfWork) -> None:
    with uow, pytest.raises(ValueError):
        uow.categories.update(Category(id=None, name="Ghost"))


def test_category_with_expenses_cannot_be_deleted(uow: SqlAlchemyUnitOfWork) -> None:
    category = add_category(uow)
    assert category.id is not None
    with uow:
        uow.expenses.add(new_expense(category.id))
        uow.commit()
    with uow:
        assert uow.expenses.count_for_category(category.id) == 1
        with pytest.raises(IntegrityError):
            uow.categories.delete(category.id)


# --- Expenses -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "currency", "fx_rate"),
    [
        (Decimal("1250.50"), Currency.INR, Decimal("1")),
        (Decimal("0.1") + Decimal("0.2"), Currency.INR, Decimal("1")),
        (Decimal("0.01"), Currency.INR, Decimal("1")),
        (Decimal("9999999.99"), Currency.INR, Decimal("1")),
        (Decimal("45.00"), Currency.USD, Decimal("83.123456")),
    ],
)
def test_money_round_trips_exactly(
    uow: SqlAlchemyUnitOfWork, amount: Decimal, currency: Currency, fx_rate: Decimal
) -> None:
    category = add_category(uow)
    assert category.id is not None
    expense = new_expense(category.id, amount=amount, currency=currency, fx_rate=fx_rate)
    with uow:
        saved = uow.expenses.add(expense)
        uow.commit()
    assert saved.id is not None

    with uow:
        loaded = uow.expenses.get(saved.id)
    assert loaded is not None
    assert loaded.amount == amount
    assert loaded.fx_rate == fx_rate
    assert loaded.amount_base == expense.amount_base
    assert isinstance(loaded.amount, Decimal)
    assert loaded.currency is currency
    assert loaded.created_at is not None and loaded.created_at.tzinfo is not None


def test_expense_update_and_delete(uow: SqlAlchemyUnitOfWork) -> None:
    category = add_category(uow)
    assert category.id is not None
    with uow:
        saved = uow.expenses.add(new_expense(category.id))
        uow.commit()
    assert saved.id is not None

    changed = Expense.create(
        category_id=category.id,
        amount=Decimal("10.00"),
        currency=Currency.USD,
        fx_rate=Decimal("83.5"),
        description="changed",
        spent_on=date(2026, 9, 2),
    )
    changed.id = saved.id
    with uow:
        updated = uow.expenses.update(changed)
        uow.commit()
    assert (updated.amount, updated.currency, updated.amount_base) == (
        Decimal("10.00"),
        Currency.USD,
        Decimal("835.00"),
    )

    with uow:
        assert uow.expenses.delete(saved.id) is True
        uow.commit()
    with uow:
        assert uow.expenses.get(saved.id) is None


def test_expense_list_filters_sorts_and_paginates(uow: SqlAlchemyUnitOfWork) -> None:
    food, travel = add_category(uow, "Food"), add_category(uow, "Travel")
    assert food.id is not None and travel.id is not None
    with uow:
        for day in range(1, 6):
            uow.expenses.add(new_expense(food.id, spent_on=date(2026, 9, day)))
        uow.expenses.add(new_expense(travel.id, spent_on=date(2026, 8, 31)))
        uow.commit()

    with uow:
        page1, total = uow.expenses.list(page=1, page_size=2)
        page3, _ = uow.expenses.list(page=3, page_size=2)
        by_category, food_total = uow.expenses.list(category_id=food.id)
        in_range, range_total = uow.expenses.list(
            date_from=date(2026, 9, 2), date_to=date(2026, 9, 4)
        )

    assert total == 6
    assert [e.spent_on.day for e in page1] == [5, 4]
    assert [e.spent_on for e in page3] == [date(2026, 9, 1), date(2026, 8, 31)]
    assert food_total == 5 and all(e.category_id == food.id for e in by_category)
    assert range_total == 3 and [e.spent_on.day for e in in_range] == [4, 3, 2]


def test_expense_list_rejects_invalid_page(uow: SqlAlchemyUnitOfWork) -> None:
    with uow, pytest.raises(ValueError):
        uow.expenses.list(page=0)


# --- session_scope / ping -------------------------------------------------------


def test_session_scope_commits_and_rolls_back(session_factory: SessionFactory) -> None:
    with session_scope(session_factory) as session:
        session.add(CategoryRow(name="Kept"))
    with pytest.raises(RuntimeError), session_scope(session_factory) as session:
        session.add(CategoryRow(name="Dropped"))
        raise RuntimeError("boom")

    with session_scope(session_factory) as session:
        names = set(session.scalars(select(CategoryRow.name)))
    assert "Kept" in names and "Dropped" not in names


def test_ping(engine: Engine) -> None:
    assert ping(engine) is True
    assert ping(create_db_engine("postgresql+psycopg://x:y@127.0.0.1:1/none")) is False


# --- Unit of Work ---------------------------------------------------------------


def test_uow_rolls_back_without_commit(uow: SqlAlchemyUnitOfWork) -> None:
    with uow:
        uow.categories.add(Category(id=None, name="Temp"))
    with uow:
        assert uow.categories.get_by_name("Temp") is None


def test_uow_rolls_back_on_exception(uow: SqlAlchemyUnitOfWork) -> None:
    with pytest.raises(RuntimeError), uow:
        uow.categories.add(Category(id=None, name="Temp"))
        raise RuntimeError("boom")
    with uow:
        assert uow.categories.get_by_name("Temp") is None


class AuditingPublisher:
    """Stand-in for Phase 4's EventBus: writes an audit row inside the same transaction."""

    def publish(self, event: DomainEvent, uow: AbstractUnitOfWork) -> None:
        uow.audit_logs.add(
            AuditLogEntry(
                id=None,
                event_type=event.event_type,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                payload={"count": 3},
                correlation_id="cid-1",
                occurred_at=event.occurred_at,
            )
        )


def test_events_are_audited_in_the_same_transaction(session_factory: SessionFactory) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory, publisher=AuditingPublisher())
    with uow:
        uow.record(ExpensesImported(count=3))
        uow.commit()

    with uow:
        entries, total = uow.audit_logs.list()
    assert total == 1
    assert entries[0].event_type == "ExpensesImported"
    assert entries[0].payload == {"count": 3}
    assert entries[0].occurred_at.tzinfo is not None


def test_events_discarded_on_rollback(session_factory: SessionFactory) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory, publisher=AuditingPublisher())
    with uow:
        uow.record(ExpensesImported(count=3))
    with uow:
        assert uow.audit_logs.list() == ([], 0)


def test_audit_log_list_filters_and_orders_newest_first(uow: SqlAlchemyUnitOfWork) -> None:
    def entry(entity_type: str, entity_id: int, minute: int) -> AuditLogEntry:
        return AuditLogEntry(
            id=None,
            event_type="X",
            entity_type=entity_type,
            entity_id=entity_id,
            payload={},
            correlation_id="c",
            occurred_at=datetime(2026, 9, 1, 10, minute, tzinfo=UTC),
        )

    with uow:
        uow.audit_logs.add(entry("expense", 1, 0))
        uow.audit_logs.add(entry("expense", 2, 5))
        uow.audit_logs.add(entry("category", 1, 10))
        uow.commit()

    with uow:
        all_entries, total = uow.audit_logs.list(page_size=2)
        expenses, expense_total = uow.audit_logs.list(entity_type="expense")
        one, one_total = uow.audit_logs.list(entity_type="expense", entity_id=2)

    assert total == 3 and [e.occurred_at.minute for e in all_entries] == [10, 5]
    assert expense_total == 2 and [e.entity_id for e in expenses] == [2, 1]
    assert one_total == 1 and one[0].entity_id == 2
