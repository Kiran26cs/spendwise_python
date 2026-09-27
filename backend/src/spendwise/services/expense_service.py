"""Expense use cases. FX rates are passed in (fetched asynchronously by the API layer).

Concepts: classes with injected dependencies, context managers, ``dataclasses.replace``,
``Decimal`` arithmetic, ``X | None`` optional arguments, keyword-only arguments.
"""

from dataclasses import replace
from datetime import date
from decimal import Decimal

from spendwise.core.decorators import log_execution_time
from spendwise.core.exceptions import DomainValidationError, NotFoundError
from spendwise.domain.events import ExpenseCreated, ExpenseDeleted, ExpenseUpdated
from spendwise.domain.models import Currency, Expense
from spendwise.infrastructure.unit_of_work import AbstractUnitOfWork


class ExpenseService:
    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self.uow = uow

    def list(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        date_from: date | None = None,
        date_to: date | None = None,
        category_id: int | None = None,
    ) -> tuple[list[Expense], int]:
        if date_from and date_to and date_from > date_to:
            raise DomainValidationError("date_from must not be after date_to")
        with self.uow:
            return self.uow.expenses.list(
                page=page,
                page_size=page_size,
                date_from=date_from,
                date_to=date_to,
                category_id=category_id,
            )

    def get(self, expense_id: int) -> Expense:
        with self.uow:
            return self._get_or_raise(expense_id)

    @log_execution_time
    def create(
        self,
        *,
        category_id: int,
        amount: Decimal,
        currency: Currency,
        fx_rate: Decimal,
        description: str,
        spent_on: date,
    ) -> Expense:
        """Create an expense; ``fx_rate`` converts ``currency`` to the base currency."""
        expense = Expense.create(
            category_id=category_id,
            amount=amount,
            currency=currency,
            fx_rate=fx_rate,
            description=description,
            spent_on=spent_on,
        )
        with self.uow:
            self._ensure_category_exists(category_id)
            created = self.uow.expenses.add(expense)
            self.uow.record(self._event(ExpenseCreated, created))
            self.uow.commit()
        return created

    @log_execution_time
    def update(
        self,
        expense_id: int,
        *,
        category_id: int,
        amount: Decimal,
        currency: Currency,
        description: str,
        spent_on: date,
        fx_rate: Decimal | None = None,
    ) -> Expense:
        """Replace an expense's fields.

        Pass ``fx_rate`` when the currency changes. When it is ``None`` the stored rate is
        reused, and ``amount_base`` is recomputed from the (possibly new) amount.
        """
        with self.uow:
            existing = self._get_or_raise(expense_id)
            if fx_rate is None:
                if currency != existing.currency:
                    raise DomainValidationError("fx_rate is required when the currency changes")
                fx_rate = existing.fx_rate
            self._ensure_category_exists(category_id)
            changed = replace(
                Expense.create(
                    category_id=category_id,
                    amount=amount,
                    currency=currency,
                    fx_rate=fx_rate,
                    description=description,
                    spent_on=spent_on,
                ),
                id=expense_id,
                created_at=existing.created_at,
            )
            updated = self.uow.expenses.update(changed)
            self.uow.record(self._event(ExpenseUpdated, updated))
            self.uow.commit()
        return updated

    @log_execution_time
    def delete(self, expense_id: int) -> None:
        with self.uow:
            self._get_or_raise(expense_id)
            self.uow.expenses.delete(expense_id)
            self.uow.record(ExpenseDeleted(expense_id=expense_id))
            self.uow.commit()

    def _get_or_raise(self, expense_id: int) -> Expense:
        expense = self.uow.expenses.get(expense_id)
        if expense is None:
            raise NotFoundError(f"Expense {expense_id} not found")
        return expense

    def _ensure_category_exists(self, category_id: int) -> None:
        if self.uow.categories.get(category_id) is None:
            raise DomainValidationError(f"Category {category_id} does not exist")

    @staticmethod
    def _event[E: (ExpenseCreated, ExpenseUpdated)](event_type: type[E], expense: Expense) -> E:
        assert expense.id is not None
        return event_type(
            expense_id=expense.id,
            category_id=expense.category_id,
            amount=expense.amount,
            currency=expense.currency,
            amount_base=expense.amount_base,
            spent_on=expense.spent_on,
        )
