"""Export a monthly report as CSV or JSON (Strategy pattern + factory with a registry).

Concepts: ``Protocol``, ``ClassVar``, ``csv`` and ``json`` modules, ``io.StringIO``,
``itertools.chain``, ``functools.partial``, dict registries, ``match`` statements.
"""

import csv
import io
import json
from collections.abc import Iterator
from decimal import Decimal
from functools import partial
from itertools import chain
from typing import Any, ClassVar, Protocol

from spendwise.core.exceptions import DomainValidationError
from spendwise.domain.models import ExportFormat
from spendwise.services.report_service import CategoryTotal, MonthlyReport


class Exporter(Protocol):
    media_type: ClassVar[str]
    file_extension: ClassVar[str]

    def export(self, report: MonthlyReport) -> str: ...


def filename_for(report: MonthlyReport, exporter: Exporter) -> str:
    return f"spendwise-{report.period}.{exporter.file_extension}"


def _line_fields(line: CategoryTotal) -> dict[str, Any]:
    status = line.status
    return {
        "category_id": line.category_id,
        "category": line.category_name,
        "expense_count": line.expense_count,
        "spent": status.spent,
        "budget": status.budget,
        "remaining": status.remaining,
        "used_percent": status.used_percent,
        "over_budget": status.over_budget,
    }


class CsvExporter:
    """One row per category plus a TOTAL row. Decimals are written as exact text."""

    media_type: ClassVar[str] = "text/csv"
    file_extension: ClassVar[str] = "csv"
    columns: ClassVar[list[str]] = [
        "category",
        "expense_count",
        "spent",
        "budget",
        "remaining",
        "used_percent",
        "over_budget",
    ]

    def export(self, report: MonthlyReport) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerows(chain([self.columns], self._rows(report), [self._total_row(report)]))
        return buffer.getvalue()

    def _rows(self, report: MonthlyReport) -> Iterator[list[str]]:
        for line in report.lines:
            fields = _line_fields(line)
            yield [self._cell(fields[column]) for column in self.columns]

    def _total_row(self, report: MonthlyReport) -> list[str]:
        count = sum(line.expense_count for line in report.lines)
        return ["TOTAL", str(count), str(report.total_spent), str(report.total_budget), "", "", ""]

    @staticmethod
    def _cell(value: Any) -> str:
        match value:
            case None:
                return ""
            case bool():
                return "yes" if value else "no"
            case _:
                return str(value)


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)  # formatting only: values are within float's exact range
    raise TypeError(f"Cannot serialize {type(value).__name__}")


_dumps = partial(json.dumps, indent=2, default=_json_default)


class JsonExporter:
    """The report as a JSON document with money as JSON numbers (same contract as the API)."""

    media_type: ClassVar[str] = "application/json"
    file_extension: ClassVar[str] = "json"

    def export(self, report: MonthlyReport) -> str:
        return _dumps(
            {
                "period": report.period,
                "total_spent": report.total_spent,
                "total_budget": report.total_budget,
                "over_budget_count": report.over_budget_count,
                "categories": [_line_fields(line) for line in report.lines],
            }
        )


_EXPORTERS: dict[ExportFormat, type[Exporter]] = {
    ExportFormat.CSV: CsvExporter,
    ExportFormat.JSON: JsonExporter,
}


def get_exporter(fmt: ExportFormat | str) -> Exporter:
    """Factory: return the exporter registered for ``fmt``."""
    try:
        return _EXPORTERS[ExportFormat(fmt)]()
    except ValueError as exc:
        supported = ", ".join(f.value for f in ExportFormat)
        raise DomainValidationError(f"Unsupported format '{fmt}'. Use one of: {supported}") from exc
