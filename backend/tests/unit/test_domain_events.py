"""Domain events: naming, entity identification, immutability."""

import dataclasses
from datetime import UTC, date
from decimal import Decimal

import pytest

from spendwise.domain.events import (
    CategoryCreated,
    CategoryDeleted,
    DomainEvent,
    ExpenseCreated,
    ExpenseDeleted,
    ExpensesImported,
)
from spendwise.domain.models import Currency


def test_event_type_is_class_name() -> None:
    assert CategoryDeleted(category_id=1).event_type == "CategoryDeleted"


@pytest.mark.parametrize(
    ("event", "entity_type", "entity_id"),
    [
        (CategoryCreated(category_id=3, name="Food", monthly_budget=None), "category", 3),
        (ExpenseDeleted(expense_id=7), "expense", 7),
        (ExpensesImported(count=12), "import", None),
    ],
)
def test_entity_identification(event: DomainEvent, entity_type: str, entity_id: int | None) -> None:
    assert event.entity_type == entity_type
    assert event.entity_id == entity_id


def test_occurred_at_is_utc_and_per_instance() -> None:
    first, second = ExpensesImported(count=1), ExpensesImported(count=2)
    assert first.occurred_at.tzinfo is UTC
    assert second.occurred_at >= first.occurred_at


def test_events_are_immutable() -> None:
    event = ExpenseDeleted(expense_id=1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.expense_id = 2  # type: ignore[misc]


def test_asdict_contains_payload_fields() -> None:
    event = ExpenseCreated(
        expense_id=1,
        category_id=2,
        amount=Decimal("45.00"),
        currency=Currency.USD,
        amount_base=Decimal("3740.56"),
        spent_on=date(2026, 9, 3),
    )
    payload = dataclasses.asdict(event)
    assert payload["amount"] == Decimal("45.00")
    assert {"occurred_at", "expense_id", "category_id", "currency", "spent_on"} <= payload.keys()
    assert "entity_type" not in payload  # ClassVar is not a field
