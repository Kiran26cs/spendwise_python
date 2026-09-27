"""Category and expense services with the in-memory Unit of Work and static FX rates."""

import asyncio
from datetime import date
from decimal import Decimal

import pytest

from spendwise.core.exceptions import ConflictError, DomainValidationError, NotFoundError
from spendwise.domain.models import Currency, Expense
from spendwise.infrastructure.fx_client import StaticFxProvider
from spendwise.services.category_service import CategoryService
from spendwise.services.event_bus import create_event_bus
from spendwise.services.expense_service import ExpenseService
from tests.fakes import FakeUnitOfWork


@pytest.fixture
def uow() -> FakeUnitOfWork:
    return FakeUnitOfWork(create_event_bus())


@pytest.fixture
def categories(uow: FakeUnitOfWork) -> CategoryService:
    return CategoryService(uow)


@pytest.fixture
def expenses(uow: FakeUnitOfWork) -> ExpenseService:
    return ExpenseService(uow)


def audit_types(uow: FakeUnitOfWork) -> list[str]:
    return [e.event_type for e in uow.audit_logs.entries]


def add_expense(
    service: ExpenseService,
    category_id: int,
    amount: str = "100.00",
    currency: Currency = Currency.INR,
    fx_rate: str = "1",
) -> Expense:
    return service.create(
        category_id=category_id,
        amount=Decimal(amount),
        currency=currency,
        fx_rate=Decimal(fx_rate),
        description="test",
        spent_on=date(2026, 9, 1),
    )


# --- CategoryService --------------------------------------------------------------


def test_category_lifecycle_is_audited(categories: CategoryService, uow: FakeUnitOfWork) -> None:
    created = categories.create(name=" Food ", monthly_budget=Decimal("5000.00"))
    assert created.id is not None and created.name == "Food"

    updated = categories.update(created.id, name="Groceries", monthly_budget=None)
    assert updated.name == "Groceries" and updated.monthly_budget is None
    assert categories.get(created.id) == updated
    assert categories.list() == [updated]

    categories.delete(created.id)
    with pytest.raises(NotFoundError):
        categories.get(created.id)

    assert audit_types(uow) == ["CategoryCreated", "CategoryUpdated", "CategoryDeleted"]
    assert uow.audit_logs.entries[0].payload == {
        "category_id": created.id,
        "name": "Food",
        "monthly_budget": "5000.00",
    }


def test_duplicate_category_name_conflicts(categories: CategoryService) -> None:
    categories.create(name="Food", monthly_budget=None)
    with pytest.raises(ConflictError, match="already exists"):
        categories.create(name="FOOD", monthly_budget=None)


def test_rename_to_existing_name_conflicts(categories: CategoryService) -> None:
    categories.create(name="Food", monthly_budget=None)
    travel = categories.create(name="Travel", monthly_budget=None)
    assert travel.id is not None
    with pytest.raises(ConflictError):
        categories.update(travel.id, name="food", monthly_budget=None)
    # keeping its own name (different case) is fine
    assert categories.update(travel.id, name="TRAVEL", monthly_budget=None).name == "TRAVEL"


def test_category_in_use_cannot_be_deleted(
    categories: CategoryService, expenses: ExpenseService, uow: FakeUnitOfWork
) -> None:
    food = categories.create(name="Food", monthly_budget=None)
    assert food.id is not None
    add_expense(expenses, food.id)

    with pytest.raises(ConflictError, match="has 1 expense"):
        categories.delete(food.id)
    assert "CategoryDeleted" not in audit_types(uow)


@pytest.mark.parametrize("action", ["update", "delete"])
def test_missing_category_not_found(categories: CategoryService, action: str) -> None:
    with pytest.raises(NotFoundError, match="Category 99 not found"):
        if action == "update":
            categories.update(99, name="X", monthly_budget=None)
        else:
            categories.delete(99)


def test_invalid_category_raises_before_touching_storage(
    categories: CategoryService, uow: FakeUnitOfWork
) -> None:
    with pytest.raises(DomainValidationError):
        categories.create(name="   ", monthly_budget=None)
    assert uow.audit_logs.entries == []


# --- ExpenseService ---------------------------------------------------------------


@pytest.fixture
def food_id(categories: CategoryService) -> int:
    food = categories.create(name="Food", monthly_budget=None)
    assert food.id is not None
    return food.id


def test_expense_lifecycle_is_audited(
    expenses: ExpenseService, food_id: int, uow: FakeUnitOfWork
) -> None:
    created = add_expense(expenses, food_id)
    assert created.id is not None
    updated = expenses.update(
        created.id,
        category_id=food_id,
        amount=Decimal("150.00"),
        currency=Currency.INR,
        description="more",
        spent_on=date(2026, 9, 2),
    )
    assert updated.amount_base == Decimal("150.00")
    expenses.delete(created.id)

    assert audit_types(uow)[-3:] == ["ExpenseCreated", "ExpenseUpdated", "ExpenseDeleted"]
    assert uow.audit_logs.entries[-2].payload["amount"] == "150.00"


def test_usd_to_inr_conversion_rounds_half_up(expenses: ExpenseService, food_id: int) -> None:
    fx = StaticFxProvider({Currency.USD: Decimal("0.125")})  # 1 USD = 0.125 INR (test rate)
    rate = asyncio.run(fx.get_rate(Currency.USD, Currency.INR))

    expense = add_expense(
        expenses, food_id, amount="1.00", currency=Currency.USD, fx_rate=str(rate)
    )

    # 1.00 * 0.125 = 0.125 -> 0.13 (half up; banker's rounding would give 0.12)
    assert expense.amount_base == Decimal("0.13")
    assert expense.fx_rate == Decimal("0.125")


def test_amount_change_reuses_stored_rate(expenses: ExpenseService, food_id: int) -> None:
    created = add_expense(expenses, food_id, amount="10.00", currency=Currency.USD, fx_rate="83.5")
    assert created.id is not None

    updated = expenses.update(
        created.id,
        category_id=food_id,
        amount=Decimal("20.00"),
        currency=Currency.USD,
        description="taxi",
        spent_on=date(2026, 9, 1),
    )

    assert updated.fx_rate == Decimal("83.5")
    assert updated.amount_base == Decimal("1670.00")
    assert updated.created_at == created.created_at


def test_currency_change_requires_rate(expenses: ExpenseService, food_id: int) -> None:
    created = add_expense(expenses, food_id)
    assert created.id is not None
    kwargs = {
        "category_id": food_id,
        "amount": Decimal("10.00"),
        "currency": Currency.EUR,
        "description": "",
        "spent_on": date(2026, 9, 1),
    }

    with pytest.raises(DomainValidationError, match="fx_rate is required"):
        expenses.update(created.id, **kwargs)  # type: ignore[arg-type]

    updated = expenses.update(created.id, fx_rate=Decimal("90"), **kwargs)  # type: ignore[arg-type]
    assert (updated.currency, updated.amount_base) == (Currency.EUR, Decimal("900.00"))


def test_unknown_category_rejected(expenses: ExpenseService, food_id: int) -> None:
    with pytest.raises(DomainValidationError, match="Category 42 does not exist"):
        add_expense(expenses, 42)
    created = add_expense(expenses, food_id)
    assert created.id is not None
    with pytest.raises(DomainValidationError, match="Category 42"):
        expenses.update(
            created.id,
            category_id=42,
            amount=Decimal("1"),
            currency=Currency.INR,
            description="",
            spent_on=date(2026, 9, 1),
        )


@pytest.mark.parametrize("action", ["get", "delete"])
def test_missing_expense_not_found(expenses: ExpenseService, action: str) -> None:
    with pytest.raises(NotFoundError, match="Expense 5 not found"):
        getattr(expenses, action)(5)


def test_list_filters_and_validates_range(expenses: ExpenseService, food_id: int) -> None:
    add_expense(expenses, food_id)
    items, total = expenses.list(category_id=food_id, date_from=date(2026, 9, 1))
    assert total == 1 and len(items) == 1

    with pytest.raises(DomainValidationError, match="date_from"):
        expenses.list(date_from=date(2026, 9, 2), date_to=date(2026, 9, 1))
