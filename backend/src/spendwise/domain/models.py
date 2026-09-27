"""Domain entities (Category, Expense), value objects (BudgetStatus) and money rules.

Validation runs in ``__post_init__``; change an entity with ``dataclasses.replace`` so the
rules run again (plain attribute assignment is not re-validated).

Concepts: ``dataclasses`` (``frozen``, ``__post_init__``), ``StrEnum``, ``Decimal`` with
explicit rounding, ``datetime.date``, ``@property``, ``@classmethod``, ``__str__``,
keyword-only args, custom exceptions.
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any

from spendwise.core.exceptions import DomainValidationError

MONEY_QUANTUM = Decimal("0.01")
MAX_MONEY = Decimal("9999999999.99")  # NUMERIC(12, 2)
MAX_FX_RATE = Decimal("999999999999.999999")  # NUMERIC(18, 6)
MAX_CATEGORY_NAME = 50
MAX_DESCRIPTION = 200


class Currency(StrEnum):
    """Supported ISO 4217 currency codes."""

    INR = "INR"
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    AED = "AED"
    SGD = "SGD"


class ExportFormat(StrEnum):
    """File formats the monthly report can be exported to."""

    CSV = "csv"
    JSON = "json"


BASE_CURRENCY = Currency.INR


def round_money(value: Decimal) -> Decimal:
    """Round to 2 decimal places, half up. The single rounding rule for money."""
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _decimal_places(value: Decimal) -> int:
    """Significant decimal places; trailing zeros don't count (``12.340`` -> 2)."""
    exponent = value.normalize().as_tuple().exponent
    assert isinstance(exponent, int)  # finite values only; checked by callers
    return max(0, -exponent)


def _validate_decimal(
    field: str,
    value: Decimal,
    *,
    places: int,
    maximum: Decimal,
    allow_zero: bool = False,
) -> None:
    """Raise ``DomainValidationError`` unless ``value`` is an exact, in-range Decimal."""
    if isinstance(value, bool) or not isinstance(value, Decimal):
        raise DomainValidationError(f"{field} must be a Decimal, got {type(value).__name__}")
    if not value.is_finite():
        raise DomainValidationError(f"{field} must be a finite number")
    if value < 0 or (value == 0 and not allow_zero):
        bound = "zero or greater" if allow_zero else "greater than zero"
        raise DomainValidationError(f"{field} must be {bound}")
    if value > maximum:
        raise DomainValidationError(f"{field} must not exceed {maximum}")
    if _decimal_places(value) > places:
        raise DomainValidationError(f"{field} must have at most {places} decimal places")


@dataclass(frozen=True)
class AuditLogEntry:
    """One row of the audit trail. ``payload`` must already be JSON-serializable."""

    id: int | None
    event_type: str
    entity_type: str
    entity_id: int | None
    payload: dict[str, Any]
    correlation_id: str
    occurred_at: datetime


@dataclass(frozen=True)
class BudgetStatus:
    """How much of a category's monthly budget has been used."""

    budget: Decimal | None
    spent: Decimal
    remaining: Decimal | None
    used_percent: Decimal | None
    over_budget: bool


@dataclass
class Category:
    """A spending category with an optional monthly budget (``None`` means no budget)."""

    id: int | None
    name: str
    monthly_budget: Decimal | None = None

    def __post_init__(self) -> None:
        self.name = self.name.strip()
        if not self.name:
            raise DomainValidationError("Category name must not be empty")
        if len(self.name) > MAX_CATEGORY_NAME:
            raise DomainValidationError(
                f"Category name must be at most {MAX_CATEGORY_NAME} characters"
            )
        if self.monthly_budget is not None:
            _validate_decimal(
                "monthly_budget", self.monthly_budget, places=2, maximum=MAX_MONEY, allow_zero=True
            )

    def budget_status(self, spent: Decimal) -> BudgetStatus:
        """Compare ``spent`` (in base currency) against this category's budget."""
        budget = self.monthly_budget
        if budget is None:
            return BudgetStatus(
                budget=None, spent=spent, remaining=None, used_percent=None, over_budget=False
            )
        used_percent = None if budget == 0 else round_money(spent / budget * 100)
        return BudgetStatus(
            budget=budget,
            spent=spent,
            remaining=budget - spent,
            used_percent=used_percent,
            over_budget=spent > budget,
        )

    def __str__(self) -> str:
        return self.name


@dataclass
class Expense:
    """A single expense. ``amount_base`` is ``amount`` converted to ``BASE_CURRENCY``."""

    id: int | None
    category_id: int
    amount: Decimal
    currency: Currency
    amount_base: Decimal
    fx_rate: Decimal
    description: str
    spent_on: date
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.category_id <= 0:
            raise DomainValidationError("category_id must be a positive integer")
        self.currency = Currency(self.currency)
        _validate_decimal("amount", self.amount, places=2, maximum=MAX_MONEY)
        _validate_decimal("fx_rate", self.fx_rate, places=6, maximum=MAX_FX_RATE)
        _validate_decimal("amount_base", self.amount_base, places=2, maximum=MAX_MONEY)
        if not self.is_foreign and self.fx_rate != 1:
            raise DomainValidationError(f"fx_rate must be 1 for {BASE_CURRENCY} expenses")
        if self.amount_base != round_money(self.amount * self.fx_rate):
            raise DomainValidationError("amount_base must equal amount * fx_rate (rounded)")
        self.description = self.description.strip()
        if len(self.description) > MAX_DESCRIPTION:
            raise DomainValidationError(f"description must be at most {MAX_DESCRIPTION} characters")
        if self.spent_on > date.today():
            raise DomainValidationError("spent_on must not be in the future")

    @classmethod
    def create(
        cls,
        *,
        category_id: int,
        amount: Decimal,
        currency: Currency,
        fx_rate: Decimal,
        description: str,
        spent_on: date,
    ) -> "Expense":
        """Build a new (unsaved) expense, computing ``amount_base`` from ``fx_rate``."""
        # Validate before multiplying so bad input raises DomainValidationError, not TypeError.
        _validate_decimal("amount", amount, places=2, maximum=MAX_MONEY)
        _validate_decimal("fx_rate", fx_rate, places=6, maximum=MAX_FX_RATE)
        return cls(
            id=None,
            category_id=category_id,
            amount=amount,
            currency=currency,
            amount_base=round_money(amount * fx_rate),
            fx_rate=fx_rate,
            description=description,
            spent_on=spent_on,
        )

    @property
    def is_foreign(self) -> bool:
        """True when the expense was not paid in the base currency."""
        return self.currency != BASE_CURRENCY

    def __str__(self) -> str:
        return f"{self.spent_on} {self.currency} {self.amount} ({self.description})"
