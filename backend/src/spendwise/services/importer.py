"""Streaming CSV import of expenses, validated row by row.

The file is never loaded whole: ``csv.DictReader`` reads it lazily, a generator turns rows
into ``Expense`` objects (collecting errors for bad rows), and valid expenses are inserted
in batches of ``BATCH_SIZE`` inside one transaction. Valid rows are imported even when
other rows fail.

Expected columns: ``spent_on,category,amount,currency,description``.

Concepts: generators (``yield``, ``yield from``), ``csv.DictReader``, ``io.TextIOWrapper``,
``itertools.batched``, ``collections.Counter``, ``try/except/else``, ``raise ... from``,
``match`` statements, frozen dataclasses, f-strings.
"""

import csv
import io
import logging
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from itertools import batched
from typing import BinaryIO

from spendwise.core.decorators import log_execution_time
from spendwise.core.exceptions import DomainValidationError
from spendwise.domain.events import ExpensesImported
from spendwise.domain.models import BASE_CURRENCY, Category, Currency, Expense
from spendwise.infrastructure.unit_of_work import AbstractUnitOfWork

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("spent_on", "category", "amount")
OPTIONAL_COLUMNS = ("currency", "description")
BATCH_SIZE = 100


@dataclass(frozen=True)
class RowError:
    row: int  # 1-based line number in the file (the header is row 1)
    message: str


@dataclass
class ImportResult:
    imported: int = 0
    errors: list[RowError] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return len(self.errors)


class _InvalidRowError(Exception):
    """Internal: one row failed validation (becomes a RowError, not an HTTP error)."""


def _read_rows(stream: BinaryIO) -> Iterator[tuple[int, dict[str, str]]]:
    """Yield ``(row_number, row)`` lazily; the header must contain the required columns."""
    text = io.TextIOWrapper(stream, encoding="utf-8-sig", newline="")  # -sig strips a BOM
    try:
        reader = csv.DictReader(text)
        header = [name.strip().lower() for name in reader.fieldnames or []]
        missing = [column for column in REQUIRED_COLUMNS if column not in header]
        if missing:
            raise DomainValidationError(f"CSV is missing required column(s): {', '.join(missing)}")
        reader.fieldnames = header
        for row in reader:
            # reader.line_num counts physical lines, so it stays right for quoted newlines
            yield reader.line_num, {key: (value or "").strip() for key, value in row.items() if key}
    except UnicodeDecodeError as exc:
        raise DomainValidationError("CSV file must be UTF-8 encoded") from exc
    except csv.Error as exc:
        raise DomainValidationError(f"Malformed CSV: {exc}") from exc
    finally:
        text.detach()  # leave the underlying upload stream open for its owner


def _parse_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise _InvalidRowError(f"spent_on '{text}' is not a date (use YYYY-MM-DD)") from exc


def _parse_amount(text: str) -> Decimal:
    try:
        amount = Decimal(text)  # straight from text: no float step
    except InvalidOperation as exc:
        raise _InvalidRowError(f"amount '{text}' is not a number") from exc
    return amount


def _parse_currency(text: str) -> Currency:
    match text.upper():
        case "":
            return BASE_CURRENCY
        case code if code in Currency.__members__:
            return Currency(code)
        case _:
            raise _InvalidRowError(f"currency '{text}' is not supported")


def _to_expense(
    row: Mapping[str, str],
    categories: Mapping[str, Category],
    rates: Mapping[Currency, Decimal],
) -> Expense:
    """Validate one row and build an (unsaved) expense. Raises ``_InvalidRowError``."""
    if not row.get("category"):
        raise _InvalidRowError("category is required")
    category = categories.get(row["category"].lower())
    if category is None or category.id is None:
        raise _InvalidRowError(f"unknown category '{row['category']}'")
    currency = _parse_currency(row.get("currency", ""))
    fx_rate = Decimal(1) if currency == BASE_CURRENCY else rates.get(currency)
    if fx_rate is None:
        raise _InvalidRowError(f"no exchange rate available for {currency}")
    try:
        return Expense.create(
            category_id=category.id,
            amount=_parse_amount(row["amount"]),
            currency=currency,
            fx_rate=fx_rate,
            description=row.get("description", ""),
            spent_on=_parse_date(row["spent_on"]),
        )
    except DomainValidationError as exc:
        raise _InvalidRowError(exc.detail) from exc


def _valid_expenses(
    rows: Iterator[tuple[int, dict[str, str]]],
    categories: Mapping[str, Category],
    rates: Mapping[Currency, Decimal],
    errors: list[RowError],
) -> Iterator[Expense]:
    """Yield expenses for valid rows; append a ``RowError`` for each invalid one."""
    for row_number, row in rows:
        try:
            expense = _to_expense(row, categories, rates)
        except _InvalidRowError as exc:
            errors.append(RowError(row=row_number, message=str(exc)))
        else:
            yield expense


class ExpenseImporter:
    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self.uow = uow

    @log_execution_time
    def import_csv(
        self, stream: BinaryIO, *, rates_to_base: Mapping[Currency, Decimal]
    ) -> ImportResult:
        """Import expenses from a CSV stream.

        Args:
            stream: The uploaded file (binary, UTF-8).
            rates_to_base: FX rate to the base currency for each foreign currency. Rows in a
                currency without a rate fail individually.
        """
        result = ImportResult()
        with self.uow:
            categories = {c.name.lower(): c for c in self.uow.categories.list()}
            expenses = _valid_expenses(_read_rows(stream), categories, rates_to_base, result.errors)
            for batch in batched(expenses, BATCH_SIZE):
                result.imported += self.uow.expenses.add_many(batch)
            if result.imported:
                self.uow.record(ExpensesImported(count=result.imported))
                self.uow.commit()

        top_errors = Counter(error.message for error in result.errors).most_common(3)
        logger.info(
            "CSV import finished: %d imported, %d failed",
            result.imported,
            result.failed,
            extra={"imported": result.imported, "failed": result.failed, "top_errors": top_errors},
        )
        return result
