"""Reports, exports, CSV import endpoint and the seed script against PostgreSQL."""

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scripts import seed
from spendwise.api.deps import get_fx_provider
from spendwise.core.exceptions import ExternalServiceError
from spendwise.domain.models import Currency
from spendwise.infrastructure.db import SessionFactory
from spendwise.infrastructure.unit_of_work import SqlAlchemyUnitOfWork

V1 = "/api/v1"
SAMPLE_CSV = Path(__file__).resolve().parents[2] / "sample_data" / "expenses_sample.csv"


def upload(api: TestClient, content: bytes, name: str = "expenses.csv") -> dict[str, object]:
    response = api.post(f"{V1}/imports/expenses", files={"file": (name, content, "text/csv")})
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


@pytest.fixture
def seeded(session_factory: SessionFactory) -> None:
    seed.seed_categories(session_factory)


# --- Import -----------------------------------------------------------------------------


def test_sample_csv_imports_cleanly(api: TestClient, seeded: None) -> None:
    result = upload(api, SAMPLE_CSV.read_bytes())
    assert result == {"imported": 12, "failed": 0, "errors": []}

    expenses = api.get(f"{V1}/expenses", params={"page_size": 100}).json()
    assert expenses["total"] == 12
    taxi = next(e for e in expenses["items"] if e["description"] == "Airport taxi")
    assert (taxi["currency"], taxi["fx_rate"], taxi["amount_base"]) == ("USD", 83, 3735)

    audit = api.get(f"{V1}/audit-logs", params={"entity_type": "import"}).json()
    assert audit["total"] == 1
    assert audit["items"][0]["payload"] == {"count": 12}


def test_mixed_rows_return_row_errors(api: TestClient, seeded: None) -> None:
    content = (
        b"spent_on,category,amount,currency,description\n"
        b"2026-09-01,Groceries,10.00,INR,ok\n"
        b"2026-09-01,Pets,10.00,INR,bad\n"
    )
    assert upload(api, content) == {
        "imported": 1,
        "failed": 1,
        "errors": [{"row": 3, "message": "unknown category 'Pets'"}],
    }


def test_bad_file_returns_422(api: TestClient) -> None:
    response = api.post(
        f"{V1}/imports/expenses", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")}
    )
    assert response.status_code == 422
    assert "missing required column" in response.json()["detail"]


def test_upload_size_limit(api: TestClient) -> None:
    app = api.app
    app.state.settings = app.state.settings.model_copy(update={"max_upload_mb": 0})  # type: ignore[attr-defined]
    response = api.post(f"{V1}/imports/expenses", files={"file": ("x.csv", b"x", "text/csv")})
    assert response.status_code == 413
    assert response.json()["detail"] == "File exceeds the 0 MB limit"


def test_missing_file_returns_422(api: TestClient) -> None:
    assert api.post(f"{V1}/imports/expenses").status_code == 422


class DownFxProvider:
    async def get_rate(self, from_: Currency, to: Currency) -> Decimal:
        raise ExternalServiceError("FX down")

    async def get_rates(self, base: Currency, symbols: object) -> dict[Currency, Decimal]:
        raise ExternalServiceError("FX down")


class NoAedFxProvider:
    """Like the live Frankfurter API: every currency except AED."""

    async def get_rate(self, from_: Currency, to: Currency) -> Decimal:
        if Currency.AED in (from_, to):
            raise ExternalServiceError("AED not supported")
        return Decimal("80")

    async def get_rates(self, base: Currency, symbols: object) -> dict[Currency, Decimal]:
        raise NotImplementedError


def test_one_unsupported_currency_does_not_block_others(api: TestClient, seeded: None) -> None:
    api.app.dependency_overrides[get_fx_provider] = NoAedFxProvider  # type: ignore[attr-defined]
    result = upload(
        api,
        b"spent_on,category,amount,currency\n"
        b"2026-09-01,Travel,5.00,USD\n2026-09-01,Travel,5.00,AED\n",
    )
    assert result["imported"] == 1
    assert result["errors"] == [{"row": 3, "message": "no exchange rate available for AED"}]


class BrokenFxProvider:
    async def get_rate(self, from_: Currency, to: Currency) -> Decimal:
        raise RuntimeError("bug")

    async def get_rates(self, base: Currency, symbols: object) -> dict[Currency, Decimal]:
        raise NotImplementedError


def test_unexpected_fx_errors_are_not_swallowed(api: TestClient) -> None:
    api.app.dependency_overrides[get_fx_provider] = BrokenFxProvider  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="bug"):
        api.post(f"{V1}/imports/expenses", files={"file": ("x.csv", b"x", "text/csv")})


def test_import_survives_fx_outage(api: TestClient, seeded: None) -> None:
    api.app.dependency_overrides[get_fx_provider] = DownFxProvider  # type: ignore[attr-defined]
    result = upload(
        api,
        b"spent_on,category,amount,currency\n"
        b"2026-09-01,Groceries,10.00,INR\n2026-09-01,Travel,5.00,USD\n",
    )
    assert result["imported"] == 1
    assert result["errors"] == [{"row": 3, "message": "no exchange rate available for USD"}]


# --- Reports & export ---------------------------------------------------------------------


def test_monthly_report(api: TestClient, seeded: None) -> None:
    upload(api, SAMPLE_CSV.read_bytes())
    report = api.get(f"{V1}/reports/monthly", params={"year": 2026, "month": 9}).json()

    lines = {line["category_name"]: line for line in report["categories"]}
    assert len(lines) == 6
    assert lines["Groceries"]["spent"] == 4330.75  # 1250.50 + 980.25 + 2100.00
    assert lines["Groceries"]["used_percent"] == 54.13
    assert lines["Travel"]["spent"] == 14535  # 45 USD * 83 + 120 EUR * 90
    assert lines["Travel"]["over_budget"] is True
    assert lines["Travel"]["remaining"] == -9535
    assert lines["Health"]["budget"] is None
    assert lines["Entertainment"]["spent"] == 2177.95  # 499 + 15.99 GBP * 105 > 2000 budget
    assert report["over_budget_count"] == 2
    assert Decimal(str(report["total_spent"])) == Decimal("25869.45")

    empty = api.get(f"{V1}/reports/monthly", params={"year": 2026, "month": 1}).json()
    assert empty["total_spent"] == 0


@pytest.mark.parametrize(("month", "year"), [(0, 2026), (13, 2026), (1, 1999)])
def test_monthly_report_validates_period(api: TestClient, month: int, year: int) -> None:
    response = api.get(f"{V1}/reports/monthly", params={"year": year, "month": month})
    assert response.status_code == 422


def test_export_csv_and_json(api: TestClient, seeded: None) -> None:
    upload(api, SAMPLE_CSV.read_bytes())
    params = {"year": 2026, "month": 9}

    csv_response = api.get(f"{V1}/reports/monthly/export", params=params)
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert (
        csv_response.headers["content-disposition"]
        == 'attachment; filename="spendwise-2026-09.csv"'
    )
    lines = csv_response.text.splitlines()
    assert lines[0].startswith("category,expense_count,spent")
    assert lines[-1].startswith("TOTAL,12,25869.45,22000.00,")

    json_response = api.get(f"{V1}/reports/monthly/export", params={**params, "format": "json"})
    assert json_response.headers["content-type"] == "application/json"
    assert json_response.json()["period"] == "2026-09"

    bad = api.get(f"{V1}/reports/monthly/export", params={**params, "format": "xml"})
    assert bad.status_code == 422


# --- Seed script ----------------------------------------------------------------------------


def test_seed_script(session_factory: SessionFactory) -> None:
    uow = SqlAlchemyUnitOfWork(session_factory)
    seed.run(seed.parse_args(["--categories", "--expenses", "25", "--seed", "7"]), session_factory)
    with uow:
        categories = uow.categories.list()
        _, total = uow.expenses.list()
        _, audited = uow.audit_logs.list()
    assert len(categories) == len(seed.DEFAULT_CATEGORIES)
    assert total == 25
    assert audited == len(seed.DEFAULT_CATEGORIES) + 25

    seed.run(seed.parse_args(["--categories"]), session_factory)  # idempotent
    seed.run(seed.parse_args(["--reset"]), session_factory)
    with uow:
        assert uow.categories.list() == []
        assert uow.audit_logs.list() == ([], 0)


def test_seed_expenses_requires_categories(session_factory: SessionFactory) -> None:
    with pytest.raises(SystemExit, match="run with --categories first"):
        seed.run(seed.parse_args(["--expenses", "1"]), session_factory)


@pytest.mark.parametrize("argv", [[], ["--expenses", "-1"], ["--categories", "--days", "0"]])
def test_seed_rejects_bad_arguments(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        seed.parse_args(argv)
