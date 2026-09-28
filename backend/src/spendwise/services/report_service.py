"""Monthly spending report: totals per category, budget usage and grand totals.

The data flows through a lazy pipeline: the repository streams expenses sorted by category
(a generator), ``itertools.groupby`` groups them, and ``functools.reduce`` sums each group.
All arithmetic is ``Decimal``.

Concepts: generators and generator expressions, ``itertools.groupby``, ``functools.reduce``,
``operator`` helpers, ``collections.defaultdict``, ``calendar``, frozen dataclasses,
dict/list comprehensions, ``@property``.
"""

import calendar
import operator
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import reduce
from itertools import groupby

from spendwise.core.decorators import log_execution_time
from spendwise.core.exceptions import DomainValidationError
from spendwise.domain.models import BudgetStatus, Expense
from spendwise.infrastructure.unit_of_work import AbstractUnitOfWork

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class CategoryTotal:
    """One report line: a category's spending for the month and its budget status."""

    category_id: int
    category_name: str
    expense_count: int
    status: BudgetStatus


@dataclass(frozen=True)
class MonthlyReport:
    year: int
    month: int
    lines: list[CategoryTotal]

    @property
    def total_spent(self) -> Decimal:
        return sum((line.status.spent for line in self.lines), ZERO)

    @property
    def total_budget(self) -> Decimal:
        """Sum of the budgets that are set (categories without a budget are skipped)."""
        return sum(
            (line.status.budget for line in self.lines if line.status.budget is not None), ZERO
        )

    @property
    def over_budget_count(self) -> int:
        return sum(1 for line in self.lines if line.status.over_budget)

    @property
    def period(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def month_bounds(year: int, month: int) -> tuple[date, date]:
    """First and last day of the month."""
    if not 1 <= month <= 12:
        raise DomainValidationError("month must be between 1 and 12")
    if not 2000 <= year <= 2100:
        raise DomainValidationError("year must be between 2000 and 2100")
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def totals_by_category(expenses: Iterable[Expense]) -> dict[int, tuple[int, Decimal]]:
    """Map category_id -> (expense count, total in base currency).

    ``expenses`` must be sorted by category_id, as ``groupby`` only groups adjacent items.
    """
    totals: dict[int, tuple[int, Decimal]] = {}
    for category_id, group in groupby(expenses, key=operator.attrgetter("category_id")):
        amounts = [expense.amount_base for expense in group]
        totals[category_id] = (len(amounts), reduce(operator.add, amounts, ZERO))
    return totals


class ReportService:
    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self.uow = uow

    @log_execution_time
    def monthly(self, year: int, month: int) -> MonthlyReport:
        """Build the report for one month, including categories with no spending."""
        start, end = month_bounds(year, month)
        with self.uow:
            categories = self.uow.categories.list()
            # Consume the streaming generator while the transaction is still open.
            expenses = self.uow.expenses.iter_between(start, end)
            # Categories without expenses default to (0 expenses, 0.00 spent).
            totals: defaultdict[int, tuple[int, Decimal]] = defaultdict(
                lambda: (0, ZERO), totals_by_category(expenses)
            )
        lines = [
            CategoryTotal(
                category_id=category.id,
                category_name=category.name,
                expense_count=totals[category.id][0],
                status=category.budget_status(totals[category.id][1]),
            )
            for category in categories
            if category.id is not None
        ]
        return MonthlyReport(year=year, month=month, lines=lines)
