"""EventBus dispatch rules, JSON-safe payloads and the audit handler."""

from datetime import UTC, date, datetime
from decimal import Decimal

from spendwise.core.context import correlation_id_var
from spendwise.domain.events import (
    CategoryDeleted,
    CategoryEvent,
    DomainEvent,
    ExpenseCreated,
)
from spendwise.domain.models import Currency
from spendwise.infrastructure.unit_of_work import AbstractUnitOfWork
from spendwise.services.event_bus import EventBus, create_event_bus, to_json_safe
from tests.fakes import FakeUnitOfWork


def test_handlers_receive_subclass_events_in_mro_order() -> None:
    bus = EventBus()
    calls: list[str] = []
    bus.subscribe(CategoryDeleted, lambda e, u: calls.append("exact"))
    bus.subscribe(CategoryEvent, lambda e, u: calls.append("category"))
    bus.subscribe(DomainEvent, lambda e, u: calls.append("any"))
    bus.subscribe(ExpenseCreated, lambda e, u: calls.append("unrelated"))

    bus.publish(CategoryDeleted(category_id=1), FakeUnitOfWork())

    assert calls == ["exact", "category", "any"]


def test_to_json_safe_keeps_exact_values() -> None:
    value = {
        "amount": Decimal("1250.50"),
        "currency": Currency.USD,
        "spent_on": date(2026, 9, 1),
        "at": datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        "nested": [Decimal("0.1"), (1, None)],
    }
    assert to_json_safe(value) == {
        "amount": "1250.50",
        "currency": "USD",
        "spent_on": "2026-09-01",
        "at": "2026-09-01T10:00:00+00:00",
        "nested": ["0.1", [1, None]],
    }


def test_audit_handler_writes_entry_with_correlation_id() -> None:
    uow: AbstractUnitOfWork = FakeUnitOfWork(create_event_bus())
    event = ExpenseCreated(
        expense_id=7,
        category_id=2,
        amount=Decimal("45.00"),
        currency=Currency.USD,
        amount_base=Decimal("3735.00"),
        spent_on=date(2026, 9, 3),
    )
    token = correlation_id_var.set("cid-audit")
    try:
        with uow:
            uow.record(event)
            uow.commit()
    finally:
        correlation_id_var.reset(token)

    (entry,), total = uow.audit_logs.list()
    assert total == 1
    assert entry.event_type == "ExpenseCreated"
    assert (entry.entity_type, entry.entity_id) == ("expense", 7)
    assert entry.correlation_id == "cid-audit"
    assert entry.occurred_at == event.occurred_at
    assert entry.payload == {
        "expense_id": 7,
        "category_id": 2,
        "amount": "45.00",
        "currency": "USD",
        "amount_base": "3735.00",
        "spent_on": "2026-09-03",
    }
    assert repr(create_event_bus()._handlers[DomainEvent][0]) == "AuditHandler()"
