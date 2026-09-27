"""Request/response DTOs: the API contract, separate from domain and ORM models.

Concepts: Pydantic models, generics (``Page[T]``), ``Field`` constraints, ``from_attributes``
mapping from domain objects, ``classmethod`` constructors, ``StrEnum`` in schemas.
"""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from spendwise.api.types import Budget, FxRate, Money
from spendwise.domain.models import Currency


class ReadModel(BaseModel):
    """Base for responses built from domain objects via ``model_validate(obj)``."""

    model_config = ConfigDict(from_attributes=True)


# --- Categories -----------------------------------------------------------------


class CategoryWrite(BaseModel):
    name: str = Field(min_length=1, max_length=50, examples=["Groceries"])
    monthly_budget: Budget | None = Field(default=None, examples=[5000.0])


class CategoryCreate(CategoryWrite):
    pass


class CategoryUpdate(CategoryWrite):
    pass


class CategoryRead(ReadModel):
    id: int
    name: str
    monthly_budget: Budget | None


# --- Expenses -------------------------------------------------------------------


class ExpenseWrite(BaseModel):
    category_id: int = Field(gt=0)
    amount: Money = Field(examples=[1250.5])
    currency: Currency = Currency.INR
    description: str = Field(default="", max_length=200, examples=["Weekly vegetables"])
    spent_on: date


class ExpenseCreate(ExpenseWrite):
    pass


class ExpenseUpdate(ExpenseWrite):
    pass


class ExpenseRead(ReadModel):
    id: int
    category_id: int
    amount: Money
    currency: Currency
    amount_base: Money
    fx_rate: FxRate
    description: str
    spent_on: date
    created_at: datetime
    updated_at: datetime


# --- Audit log / FX -------------------------------------------------------------


class AuditLogRead(ReadModel):
    id: int
    event_type: str
    entity_type: str
    entity_id: int | None
    payload: dict[str, Any]
    correlation_id: str
    occurred_at: datetime


class FxRatesRead(BaseModel):
    base: Currency
    rates: dict[Currency, FxRate]


# --- Generic wrappers -----------------------------------------------------------


class Page[T](BaseModel):
    """One page of a paginated list."""

    items: list[T]
    total: int
    page: int
    page_size: int


class ProblemDetail(BaseModel):
    """RFC 9457 error body (``application/problem+json``)."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str
    correlation_id: str
    errors: list[dict[str, Any]] | None = None
