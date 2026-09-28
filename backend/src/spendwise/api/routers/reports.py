"""``/api/v1/reports`` - monthly report as JSON, or downloaded as a CSV/JSON file.

Concepts: query parameter validation, ``Response`` with custom media type and headers,
factory usage (``get_exporter``).
"""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from spendwise.api.deps import ReportServiceDep
from spendwise.api.schemas import MonthlyReportRead, ProblemDetail
from spendwise.domain.models import ExportFormat
from spendwise.services.exporters import filename_for, get_exporter

router = APIRouter(prefix="/reports", tags=["reports"], responses={422: {"model": ProblemDetail}})

YearQuery = Annotated[int, Query(ge=2000, le=2100, examples=[2026])]
MonthQuery = Annotated[int, Query(ge=1, le=12, examples=[9])]


@router.get("/monthly")
def monthly_report(
    year: YearQuery, month: MonthQuery, service: ReportServiceDep
) -> MonthlyReportRead:
    """Totals per category (in INR), budget usage and grand totals for one month."""
    return MonthlyReportRead.from_report(service.monthly(year, month))


@router.get(
    "/monthly/export",
    response_class=Response,
    responses={
        200: {
            "content": {"text/csv": {}, "application/json": {}},
            "description": "The report as a downloadable file.",
        }
    },
)
def export_monthly_report(
    year: YearQuery,
    month: MonthQuery,
    service: ReportServiceDep,
    format: ExportFormat = ExportFormat.CSV,
) -> Response:
    report = service.monthly(year, month)
    exporter = get_exporter(format)
    return Response(
        content=exporter.export(report),
        media_type=exporter.media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename_for(report, exporter)}"'},
    )
