"""Streaming CSV import: per-row validation, batching, auditing and file-level errors."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

import pytest

from spendwise.core.exceptions import DomainValidationError
from spendwise.domain.models import Category, Currency
from spendwise.services import importer as importer_module
from spendwise.services.event_bus import create_event_bus
from spendwise.services.importer import ExpenseImporter, RowError
from tests.fakes import FakeUnitOfWork

HEADER = "spent_on,category,amount,currency,description\n"
RATES = {Currency.USD: Decimal("83.00"), Currency.EUR: Decimal("90.00")}


@pytest.fixture
def uow() -> FakeUnitOfWork:
    uow = FakeUnitOfWork(create_event_bus())
    uow.categories.add(Category(None, "Groceries"))
    uow.categories.add(Category(None, "Travel"))
    return uow


def run_import(
    uow: FakeUnitOfWork, text: str, rates: dict[Currency, Decimal] | None = None
) -> tuple[int, list[RowError]]:
    stream = io.BytesIO(text.encode("utf-8"))
    result = ExpenseImporter(uow).import_csv(
        stream, rates_to_base=RATES if rates is None else rates
    )
    assert result.failed == len(result.errors)
    assert not stream.closed  # the caller's stream is left open
    return result.imported, result.errors


def test_valid_rows_are_imported_and_audited_once(uow: FakeUnitOfWork) -> None:
    imported, errors = run_import(
        uow,
        HEADER
        + "2026-09-01,Groceries,1250.50,INR,Weekly vegetables\n"
        + "2026-09-03,travel,45.00,usd,Airport taxi\n"  # case-insensitive category/currency
        + "2026-09-04,Groceries,99.99,,\n",  # blank currency -> INR
    )

    assert (imported, errors) == (3, [])
    saved, _ = uow.expenses.list()
    taxi = next(e for e in saved if e.currency is Currency.USD)
    assert (taxi.fx_rate, taxi.amount_base) == (Decimal("83.00"), Decimal("3735.00"))
    assert [e.event_type for e in uow.audit_logs.entries] == ["ExpensesImported"]
    assert uow.audit_logs.entries[0].payload == {"count": 3}
    assert uow.committed


def test_invalid_rows_are_reported_and_valid_rows_still_imported(uow: FakeUnitOfWork) -> None:
    future = (date.today() + timedelta(days=1)).isoformat()
    imported, errors = run_import(
        uow,
        HEADER
        + "2026-09-01,Groceries,10.00,INR,ok\n"  # row 2
        + "01/09/2026,Groceries,10.00,INR,bad date\n"  # row 3
        + "2026-09-01,Pets,10.00,INR,unknown category\n"  # row 4
        + "2026-09-01,Groceries,ten,INR,bad amount\n"  # row 5
        + "2026-09-01,Groceries,12.345,INR,too precise\n"  # row 6
        + "2026-09-01,Groceries,-5,INR,negative\n"  # row 7
        + "2026-09-01,Groceries,10.00,JPY,bad currency\n"  # row 8
        + f"{future},Groceries,10.00,INR,future\n"  # row 9
        + "2026-09-01,,10.00,INR,no category\n"  # row 10
        + "2026-09-01,Travel,10.00,GBP,no rate\n"  # row 11
        + "2026-09-02,Travel,20.00,EUR,ok\n",  # row 12
    )

    assert imported == 2
    assert [(e.row, e.message) for e in errors] == [
        (3, "spent_on '01/09/2026' is not a date (use YYYY-MM-DD)"),
        (4, "unknown category 'Pets'"),
        (5, "amount 'ten' is not a number"),
        (6, "amount must have at most 2 decimal places"),
        (7, "amount must be greater than zero"),
        (8, "currency 'JPY' is not supported"),
        (9, "spent_on must not be in the future"),
        (10, "category is required"),
        (11, "no exchange rate available for GBP"),
    ]


def test_row_numbers_follow_physical_lines(uow: FakeUnitOfWork) -> None:
    _, errors = run_import(
        uow,
        HEADER + '2026-09-01,Groceries,1.00,INR,"multi\nline"\n2026-09-01,Nope,1.00,INR,x\n',
    )
    assert errors == [RowError(row=4, message="unknown category 'Nope'")]


def test_all_rows_invalid_commits_nothing(uow: FakeUnitOfWork) -> None:
    imported, errors = run_import(uow, HEADER + "2026-09-01,Nope,1.00,INR,x\n")
    assert (imported, len(errors)) == (0, 1)
    assert not uow.committed
    assert uow.audit_logs.entries == []


def test_fx_unavailable_only_fails_foreign_rows(uow: FakeUnitOfWork) -> None:
    imported, errors = run_import(
        uow, HEADER + "2026-09-01,Groceries,1.00,INR,a\n2026-09-01,Travel,1.00,USD,b\n", rates={}
    )
    assert imported == 1
    assert errors == [RowError(row=3, message="no exchange rate available for USD")]


def test_inserts_in_batches(uow: FakeUnitOfWork, monkeypatch: pytest.MonkeyPatch) -> None:
    batch_sizes: list[int] = []
    original = uow.expenses.add_many

    def spy(expenses: object) -> int:
        count = original(expenses)  # type: ignore[arg-type]
        batch_sizes.append(count)
        return count

    monkeypatch.setattr(uow.expenses, "add_many", spy)
    rows = "".join(f"2026-09-01,Groceries,{i}.00,INR,row {i}\n" for i in range(1, 251))

    imported, _ = run_import(uow, HEADER + rows)

    assert imported == 250
    assert batch_sizes == [100, 100, 50]
    assert importer_module.BATCH_SIZE == 100


def test_header_is_flexible(uow: FakeUnitOfWork) -> None:
    # BOM (Excel), upper-case names, different order, optional columns missing, extra column
    text = "﻿Amount,Category,Spent_On,Notes\n12.50,Groceries,2026-09-01,ignored\n"
    imported, errors = run_import(uow, text)
    assert (imported, errors) == (1, [])
    (saved,), _ = uow.expenses.list()
    assert (saved.currency, saved.description) == (Currency.INR, "")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "missing required column(s): spent_on, category, amount"),
        (b"date,category,amount\n", "missing required column(s): spent_on"),
        ("spent_on,category,amount\n2026-09-01,Café,1\n".encode("latin-1"), "UTF-8"),
    ],
)
def test_file_level_errors(uow: FakeUnitOfWork, content: bytes, message: str) -> None:
    with pytest.raises(
        DomainValidationError, match=message.replace("(", r"\(").replace(")", r"\)")
    ):
        ExpenseImporter(uow).import_csv(io.BytesIO(content), rates_to_base=RATES)
    assert not uow.committed


def test_malformed_csv(uow: FakeUnitOfWork) -> None:
    old_limit = csv.field_size_limit(10)  # force csv.Error on a long field
    try:
        with pytest.raises(DomainValidationError, match="Malformed CSV"):
            run_import(uow, HEADER + "2026-09-01,Groceries,1.00,INR," + "x" * 50 + "\n")
    finally:
        csv.field_size_limit(old_limit)
    assert not uow.committed
