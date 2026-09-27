"""Translate between ORM rows and pure domain objects (keeps SQLAlchemy out of the domain).

Timestamps are normalised to UTC: PostgreSQL returns them in the session's time zone.

Concepts: plain functions as adapters, keyword arguments, ``StrEnum`` conversion.
"""

from datetime import UTC, datetime

from spendwise.domain.models import AuditLogEntry, Category, Currency, Expense
from spendwise.infrastructure.db import AuditLogRow, CategoryRow, ExpenseRow


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def category_to_domain(row: CategoryRow) -> Category:
    return Category(id=row.id, name=row.name, monthly_budget=row.monthly_budget)


def category_to_row(category: Category) -> CategoryRow:
    row = CategoryRow()
    update_category_row(row, category)
    return row


def update_category_row(row: CategoryRow, category: Category) -> None:
    row.name = category.name
    row.monthly_budget = category.monthly_budget


def expense_to_domain(row: ExpenseRow) -> Expense:
    return Expense(
        id=row.id,
        category_id=row.category_id,
        amount=row.amount,
        currency=Currency(row.currency),
        amount_base=row.amount_base,
        fx_rate=row.fx_rate,
        description=row.description,
        spent_on=row.spent_on,
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )


def expense_to_row(expense: Expense) -> ExpenseRow:
    row = ExpenseRow()
    update_expense_row(row, expense)
    return row


def update_expense_row(row: ExpenseRow, expense: Expense) -> None:
    row.category_id = expense.category_id
    row.amount = expense.amount
    row.currency = expense.currency.value
    row.amount_base = expense.amount_base
    row.fx_rate = expense.fx_rate
    row.description = expense.description
    row.spent_on = expense.spent_on


def audit_log_to_domain(row: AuditLogRow) -> AuditLogEntry:
    return AuditLogEntry(
        id=row.id,
        event_type=row.event_type,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        payload=row.payload,
        correlation_id=row.correlation_id,
        occurred_at=_utc(row.occurred_at),
    )


def audit_log_to_row(entry: AuditLogEntry) -> AuditLogRow:
    return AuditLogRow(
        event_type=entry.event_type,
        entity_type=entry.entity_type,
        entity_id=entry.entity_id,
        payload=entry.payload,
        correlation_id=entry.correlation_id,
        occurred_at=entry.occurred_at,
    )
