"""Observer pattern: an event bus dispatching domain events to subscribed handlers.

``AuditHandler`` turns every event into an ``audit_logs`` row inside the committing
Unit of Work, so the audit entry is saved atomically with the change it describes.

Concepts: callables as first-class objects, registries (``defaultdict(list)``), the MRO
(``type.__mro__``) for subscribing to a base class, ``dataclasses.asdict``, recursion,
structural pattern matching (``match``), ``__call__``.
"""

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from spendwise.core.context import correlation_id_var
from spendwise.domain.events import DomainEvent
from spendwise.domain.models import AuditLogEntry
from spendwise.infrastructure.unit_of_work import AbstractUnitOfWork

logger = logging.getLogger(__name__)

type Handler = Callable[[DomainEvent, AbstractUnitOfWork], None]


class EventBus:
    """Maps event types to handlers. Subscribing to a base class receives all its subclasses."""

    def __init__(self) -> None:
        self._handlers: defaultdict[type[DomainEvent], list[Handler]] = defaultdict(list)

    def subscribe(self, event_type: type[DomainEvent], handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def publish(self, event: DomainEvent, uow: AbstractUnitOfWork) -> None:
        """Call every handler registered for the event's class or any of its base classes."""
        for cls in type(event).__mro__:
            if not issubclass(cls, DomainEvent):
                continue
            for handler in self._handlers.get(cls, []):
                logger.debug("Dispatching %s to %r", event.event_type, handler)
                handler(event, uow)


def to_json_safe(value: Any) -> Any:
    """Recursively convert Decimals, dates, datetimes and enums into JSON-compatible values.

    Decimals become strings so the audit trail keeps the exact amount.
    """
    match value:
        case Enum():
            return value.value
        case Decimal():
            return str(value)
        case datetime() | date():
            return value.isoformat()
        case dict():
            return {key: to_json_safe(item) for key, item in value.items()}
        case list() | tuple():
            return [to_json_safe(item) for item in value]
        case _:
            return value


class AuditHandler:
    """Write an audit log row for any domain event."""

    def __call__(self, event: DomainEvent, uow: AbstractUnitOfWork) -> None:
        payload = to_json_safe(asdict(event))
        payload.pop("occurred_at", None)  # stored in its own column
        uow.audit_logs.add(
            AuditLogEntry(
                id=None,
                event_type=event.event_type,
                entity_type=event.entity_type,
                entity_id=event.entity_id,
                payload=payload,
                correlation_id=correlation_id_var.get(),
                occurred_at=event.occurred_at,
            )
        )

    def __repr__(self) -> str:
        return "AuditHandler()"


def create_event_bus() -> EventBus:
    """Build the application's event bus with all handlers registered."""
    bus = EventBus()
    bus.subscribe(DomainEvent, AuditHandler())
    return bus
