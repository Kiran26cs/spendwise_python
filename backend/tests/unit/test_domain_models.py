"""Domain rules: categories, expenses, budget status and money rounding."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from spendwise.core.exceptions import DomainValidationError
from spendwise.domain.models import (
    BudgetStatus,
    Category,
    Currency,
    Expense,
    round_money,
)

TODAY = date.today()


def make_expense(**overrides: Any) -> Expense:
    fields: dict[str, Any] = {
        "category_id": 1,
        "amount": Decimal("1250.50"),
        "currency": Currency.INR,
        "fx_rate": Decimal("1"),
        "description": "Weekly vegetables",
        "spent_on": TODAY,
    }
    fields.update(overrides)
    return Expense.create(**fields)


# --- round_money ------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.005", "1.01"),  # half up, not banker's rounding
        ("1.004", "1.00"),
        ("2.675", "2.68"),
        ("-1.005", "-1.01"),
        ("10", "10.00"),
    ],
)
def test_round_money_half_up(value: str, expected: str) -> None:
    assert round_money(Decimal(value)) == Decimal(expected)
    assert str(round_money(Decimal(value))) == expected


# --- Category ---------------------------------------------------------------


def test_category_name_is_trimmed() -> None:
    assert Category(id=None, name="  Groceries  ").name == "Groceries"


@pytest.mark.parametrize("name", ["", "   ", "x" * 51])
def test_category_rejects_invalid_names(name: str) -> None:
    with pytest.raises(DomainValidationError):
        Category(id=None, name=name)


def test_category_accepts_50_char_name() -> None:
    assert len(Category(id=None, name="x" * 50).name) == 50


@pytest.mark.parametrize("budget", [None, Decimal("0"), Decimal("0.01"), Decimal("5000.00")])
def test_category_accepts_valid_budgets(budget: Decimal | None) -> None:
    assert Category(id=None, name="Food", monthly_budget=budget).monthly_budget == budget


@pytest.mark.parametrize(
    "budget", [Decimal("-1"), Decimal("10.005"), Decimal("NaN"), Decimal("10000000000.00")]
)
def test_category_rejects_invalid_budgets(budget: Decimal) -> None:
    with pytest.raises(DomainValidationError):
        Category(id=None, name="Food", monthly_budget=budget)


def test_category_rejects_float_budget() -> None:
    with pytest.raises(DomainValidationError, match="must be a Decimal"):
        Category(id=None, name="Food", monthly_budget=10.5)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("budget", "spent", "expected"),
    [
        # under budget
        (
            "1000.00",
            "250.00",
            BudgetStatus(Decimal("1000.00"), Decimal("250.00"), Decimal("750.00"),
                         Decimal("25.00"), over_budget=False),
        ),
        # exactly at budget is not over
        (
            "1000.00",
            "1000.00",
            BudgetStatus(Decimal("1000.00"), Decimal("1000.00"), Decimal("0.00"),
                         Decimal("100.00"), over_budget=False),
        ),
        # over budget
        (
            "1000.00",
            "1200.50",
            BudgetStatus(Decimal("1000.00"), Decimal("1200.50"), Decimal("-200.50"),
                         Decimal("120.05"), over_budget=True),
        ),
        # percentage rounds half up
        (
            "3.00",
            "1.00",
            BudgetStatus(Decimal("3.00"), Decimal("1.00"), Decimal("2.00"),
                         Decimal("33.33"), over_budget=False),
        ),
        # zero budget: no percentage, any spending is over
        (
            "0",
            "0.01",
            BudgetStatus(Decimal("0"), Decimal("0.01"), Decimal("-0.01"), None, over_budget=True),
        ),
        ("0", "0", BudgetStatus(Decimal("0"), Decimal("0"), Decimal("0"), None, over_budget=False)),
    ],
)  # fmt: skip
def test_budget_status(budget: str, spent: str, expected: BudgetStatus) -> None:
    category = Category(id=1, name="Food", monthly_budget=Decimal(budget))
    assert category.budget_status(Decimal(spent)) == expected


def test_budget_status_without_budget() -> None:
    status = Category(id=1, name="Food").budget_status(Decimal("99.99"))
    assert status == BudgetStatus(None, Decimal("99.99"), None, None, over_budget=False)


# --- Expense ----------------------------------------------------------------


@pytest.mark.parametrize("amount", ["0.01", "1250.50", "9999999999.99", "12.340"])
def test_expense_accepts_valid_amounts(amount: str) -> None:
    assert make_expense(amount=Decimal(amount)).amount == Decimal(amount)


@pytest.mark.parametrize("amount", ["0", "-5", "12.345", "10000000000.00", "Infinity", "NaN"])
def test_expense_rejects_invalid_amounts(amount: str) -> None:
    with pytest.raises(DomainValidationError):
        make_expense(amount=Decimal(amount))


@pytest.mark.parametrize("amount", [12.5, 12, True, "12.50"])
def test_expense_rejects_non_decimal_amounts(amount: object) -> None:
    with pytest.raises(DomainValidationError, match="must be a Decimal"):
        make_expense(amount=amount)


def test_expense_create_converts_to_base_currency() -> None:
    expense = make_expense(
        amount=Decimal("45.00"), currency=Currency.USD, fx_rate=Decimal("83.123456")
    )
    # 45.00 * 83.123456 = 3740.55552 -> 3740.56 (half up)
    assert expense.amount_base == Decimal("3740.56")
    assert expense.is_foreign


def test_decimal_arithmetic_is_exact() -> None:
    expense = make_expense(amount=Decimal("0.1") + Decimal("0.2"))
    assert expense.amount == Decimal("0.3")


def test_base_currency_expense() -> None:
    expense = make_expense()
    assert expense.amount_base == expense.amount
    assert not expense.is_foreign


def test_base_currency_requires_rate_of_one() -> None:
    with pytest.raises(DomainValidationError, match="fx_rate must be 1"):
        make_expense(fx_rate=Decimal("2"))


@pytest.mark.parametrize("fx_rate", ["0", "-1", "1.0000001"])
def test_expense_rejects_invalid_fx_rates(fx_rate: str) -> None:
    with pytest.raises(DomainValidationError):
        make_expense(currency=Currency.USD, fx_rate=Decimal(fx_rate))


def test_expense_rejects_inconsistent_amount_base() -> None:
    expense = make_expense()
    with pytest.raises(DomainValidationError, match="amount_base"):
        replace(expense, amount_base=Decimal("1.00"))


def test_expense_currency_accepts_code_string() -> None:
    expense = make_expense(currency="USD", fx_rate=Decimal("83"))
    assert expense.currency is Currency.USD


def test_expense_rejects_unknown_currency() -> None:
    with pytest.raises(ValueError):
        make_expense(currency="XYZ")


def test_expense_rejects_future_date() -> None:
    with pytest.raises(DomainValidationError, match="future"):
        make_expense(spent_on=TODAY + timedelta(days=1))


def test_expense_accepts_today_and_past() -> None:
    assert make_expense(spent_on=TODAY).spent_on == TODAY
    assert make_expense(spent_on=date(2020, 1, 1)).spent_on == date(2020, 1, 1)


def test_expense_description_rules() -> None:
    assert make_expense(description="  taxi  ").description == "taxi"
    assert make_expense(description="").description == ""
    with pytest.raises(DomainValidationError, match="description"):
        make_expense(description="x" * 201)


@pytest.mark.parametrize("category_id", [0, -1])
def test_expense_rejects_invalid_category_id(category_id: int) -> None:
    with pytest.raises(DomainValidationError, match="category_id"):
        make_expense(category_id=category_id)


def test_replace_revalidates() -> None:
    with pytest.raises(DomainValidationError):
        replace(make_expense(), spent_on=TODAY + timedelta(days=1))


def test_string_representations() -> None:
    assert str(Category(id=1, name="Travel")) == "Travel"
    expense = make_expense(description="Taxi", spent_on=date(2026, 9, 3))
    assert str(expense) == "2026-09-03 INR 1250.50 (Taxi)"
