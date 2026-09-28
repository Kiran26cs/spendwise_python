"""Monthly report calculation and the CSV/JSON exporters."""

import json
from datetime import date
from decimal import Decimal

import pytest

from spendwise.core.exceptions import DomainValidationError
from spendwise.domain.models import Category, Currency, Expense, ExportFormat
from spendwise.services.exporters import (
    CsvExporter,
    JsonExporter,
    _json_default,
    filename_for,
    get_exporter,
)
from spendwise.services.report_service import (
    MonthlyReport,
    ReportService,
    month_bounds,
    totals_by_category,
)
from tests.fakes import FakeUnitOfWork


def expense(category_id: int, amount: str, spent_on: date, fx: str = "1") -> Expense:
    return Expense.create(
        category_id=category_id,
        amount=Decimal(amount),
        currency=Currency.INR if fx == "1" else Currency.USD,
        fx_rate=Decimal(fx),
        description="",
        spent_on=spent_on,
    )


@pytest.fixture
def report() -> MonthlyReport:
    uow = FakeUnitOfWork()
    food = uow.categories.add(Category(None, "Food", Decimal("1000.00")))
    travel = uow.categories.add(Category(None, "Travel", Decimal("500.00")))
    uow.categories.add(Category(None, "Health", None))  # no expenses, no budget
    assert food.id and travel.id
    uow.expenses.add_many(
        [
            expense(food.id, "250.50", date(2025, 6, 1)),
            expense(food.id, "0.10", date(2025, 6, 30)),
            expense(food.id, "0.20", date(2025, 6, 15)),
            expense(travel.id, "10.00", date(2025, 6, 3), fx="83.125"),  # 831.25 INR
            expense(food.id, "999.00", date(2025, 5, 31)),  # previous month: excluded
            expense(travel.id, "999.00", date(2025, 7, 1)),  # next month: excluded
        ]
    )
    return ReportService(uow).monthly(2025, 6)


def test_report_totals_per_category(report: MonthlyReport) -> None:
    lines = {line.category_name: line for line in report.lines}
    assert list(lines) == ["Food", "Health", "Travel"]

    food = lines["Food"]
    assert food.expense_count == 3
    assert food.status.spent == Decimal("250.80")  # 0.10 + 0.20 exact, no float drift
    assert food.status.remaining == Decimal("749.20")
    assert food.status.used_percent == Decimal("25.08")
    assert not food.status.over_budget

    travel = lines["Travel"]
    assert travel.status.spent == Decimal("831.25")
    assert travel.status.over_budget
    assert travel.status.used_percent == Decimal("166.25")

    health = lines["Health"]
    assert health.expense_count == 0
    assert health.status.spent == Decimal("0.00")
    assert health.status.budget is None


def test_report_grand_totals(report: MonthlyReport) -> None:
    assert report.total_spent == Decimal("1082.05")
    assert report.total_budget == Decimal("1500.00")
    assert report.over_budget_count == 1
    assert report.period == "2025-06"


def test_totals_by_category_groups_adjacent_items() -> None:
    items = [expense(1, "1.00", date(2026, 9, 1)), expense(1, "2.00", date(2026, 9, 2))]
    items.append(expense(2, "5.00", date(2026, 9, 1)))
    assert totals_by_category(iter(items)) == {1: (2, Decimal("3.00")), 2: (1, Decimal("5.00"))}


@pytest.mark.parametrize(
    ("year", "month", "expected"),
    [
        (2026, 9, (date(2026, 9, 1), date(2026, 9, 30))),
        (2028, 2, (date(2028, 2, 1), date(2028, 2, 29))),  # leap year
        (2026, 12, (date(2026, 12, 1), date(2026, 12, 31))),
    ],
)
def test_month_bounds(year: int, month: int, expected: tuple[date, date]) -> None:
    assert month_bounds(year, month) == expected


@pytest.mark.parametrize(("year", "month"), [(2026, 0), (2026, 13), (1999, 1), (2101, 1)])
def test_month_bounds_rejects_invalid(year: int, month: int) -> None:
    with pytest.raises(DomainValidationError):
        month_bounds(year, month)


# --- Exporters ------------------------------------------------------------------------


def test_csv_export(report: MonthlyReport) -> None:
    assert CsvExporter().export(report).splitlines() == [
        "category,expense_count,spent,budget,remaining,used_percent,over_budget",
        "Food,3,250.80,1000.00,749.20,25.08,no",
        "Health,0,0.00,,,,no",
        "Travel,1,831.25,500.00,-331.25,166.25,yes",
        "TOTAL,4,1082.05,1500.00,,,",
    ]


def test_json_export(report: MonthlyReport) -> None:
    data = json.loads(JsonExporter().export(report))
    assert data["period"] == "2025-06"
    assert data["total_spent"] == 1082.05
    assert data["over_budget_count"] == 1
    travel = data["categories"][2]
    assert travel == {
        "category_id": travel["category_id"],
        "category": "Travel",
        "expense_count": 1,
        "spent": 831.25,
        "budget": 500.0,
        "remaining": -331.25,
        "used_percent": 166.25,
        "over_budget": True,
    }


@pytest.mark.parametrize(
    ("fmt", "cls", "media_type"),
    [("csv", CsvExporter, "text/csv"), (ExportFormat.JSON, JsonExporter, "application/json")],
)
def test_get_exporter(fmt: str, cls: type, media_type: str, report: MonthlyReport) -> None:
    exporter = get_exporter(fmt)
    assert isinstance(exporter, cls)
    assert exporter.media_type == media_type
    assert filename_for(report, exporter) == f"spendwise-2025-06.{exporter.file_extension}"


def test_json_exporter_refuses_unknown_types() -> None:
    with pytest.raises(TypeError, match="Cannot serialize object"):
        _json_default(object())


def test_get_exporter_rejects_unknown_format() -> None:
    with pytest.raises(DomainValidationError, match="Unsupported format 'xml'"):
        get_exporter("xml")
