"""End-to-end API tests: HTTP -> router -> service -> Unit of Work -> PostgreSQL."""

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient

V1 = "/api/v1"


def create_category(api: TestClient, name: str = "Food", budget: Any = None) -> dict[str, Any]:
    response = api.post(f"{V1}/categories", json={"name": name, "monthly_budget": budget})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def create_expense(api: TestClient, category_id: int, **fields: Any) -> dict[str, Any]:
    payload = {"category_id": category_id, "amount": 100, "spent_on": "2026-09-01", **fields}
    response = api.post(f"{V1}/expenses", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def audit(api: TestClient, **params: Any) -> list[dict[str, Any]]:
    response = api.get(f"{V1}/audit-logs", params={"page_size": 100, **params})
    assert response.status_code == 200
    items: list[dict[str, Any]] = response.json()["items"]
    return items


# --- Categories -------------------------------------------------------------------


def test_category_crud(api: TestClient) -> None:
    created = create_category(api, "Groceries", 5000.5)
    assert created == {"id": created["id"], "name": "Groceries", "monthly_budget": 5000.5}
    url = f"{V1}/categories/{created['id']}"

    assert api.get(url).json() == created
    assert api.get(f"{V1}/categories").json() == [created]

    updated = api.put(url, json={"name": "Food", "monthly_budget": 0})
    assert updated.status_code == 200
    assert updated.json()["monthly_budget"] == 0

    assert api.delete(url).status_code == 204
    missing = api.get(url)
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/problem+json"
    assert missing.json()["detail"] == f"Category {created['id']} not found"


def test_duplicate_category_returns_409(api: TestClient) -> None:
    create_category(api, "Food")
    response = api.post(f"{V1}/categories", json={"name": "food"})
    assert response.status_code == 409
    assert response.json()["title"] == "Conflict"


def test_category_in_use_returns_409(api: TestClient) -> None:
    category = create_category(api)
    create_expense(api, category["id"])
    response = api.delete(f"{V1}/categories/{category['id']}")
    assert response.status_code == 409
    assert "cannot be deleted" in response.json()["detail"]


@pytest.mark.parametrize(
    "payload", [{"name": ""}, {"name": "x" * 51}, {"name": "A", "monthly_budget": -1}]
)
def test_invalid_category_returns_422(api: TestClient, payload: dict[str, Any]) -> None:
    response = api.post(f"{V1}/categories", json=payload)
    assert response.status_code == 422
    assert response.json()["errors"]


# --- Expenses ---------------------------------------------------------------------


def test_expense_crud_in_base_currency(api: TestClient) -> None:
    category = create_category(api)
    created = create_expense(api, category["id"], amount=1250.50, description=" Weekly veg ")
    assert created["amount"] == 1250.5
    assert created["amount_base"] == 1250.5
    assert created["fx_rate"] == 1
    assert created["currency"] == "INR"
    assert created["description"] == "Weekly veg"
    assert created["created_at"].endswith("Z")
    url = f"{V1}/expenses/{created['id']}"

    assert api.get(url).json() == created

    payload = {"category_id": category["id"], "amount": 99.99, "spent_on": "2026-09-02"}
    updated = api.put(url, json=payload).json()
    assert (updated["amount"], updated["amount_base"]) == (99.99, 99.99)

    assert api.delete(url).status_code == 204
    assert api.get(url).status_code == 404


def test_foreign_expense_is_converted_with_static_rate(api: TestClient) -> None:
    category = create_category(api)
    created = create_expense(api, category["id"], amount=45.00, currency="USD")
    # Static test rate: 1 USD = 83.00 INR
    assert created["fx_rate"] == 83
    assert created["amount_base"] == 3735

    # Changing currency fetches a new rate; changing only the amount reuses it.
    url = f"{V1}/expenses/{created['id']}"
    base = {"category_id": category["id"], "spent_on": "2026-09-01"}
    euro = api.put(url, json={**base, "amount": 10, "currency": "EUR"}).json()
    assert (euro["fx_rate"], euro["amount_base"]) == (90, 900)
    more = api.put(url, json={**base, "amount": 20, "currency": "EUR"}).json()
    assert (more["fx_rate"], more["amount_base"]) == (90, 1800)


@pytest.mark.parametrize("amount", [1250.50, 0.01, 9999999999.99, 0.3])
def test_money_is_accepted_and_returned_unchanged(api: TestClient, amount: float) -> None:
    category = create_category(api)
    created = create_expense(api, category["id"], amount=amount)
    fetched = api.get(f"{V1}/expenses/{created['id']}").json()
    assert Decimal(str(fetched["amount"])) == Decimal(str(amount))
    assert Decimal(str(fetched["amount_base"])) == Decimal(str(amount))


@pytest.mark.parametrize("amount", [12.345, "12.50", True, 0, -5, 10000000000.00])
def test_invalid_money_returns_422(api: TestClient, amount: Any) -> None:
    category = create_category(api)
    response = api.post(
        f"{V1}/expenses",
        json={"category_id": category["id"], "amount": amount, "spent_on": "2026-09-01"},
    )
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["errors"][0]["loc"] == ["body", "amount"]


def test_business_rule_errors_return_422(api: TestClient) -> None:
    category = create_category(api)
    future = api.post(
        f"{V1}/expenses",
        json={"category_id": category["id"], "amount": 5, "spent_on": "2999-01-01"},
    )
    unknown_category = api.post(
        f"{V1}/expenses", json={"category_id": 999999, "amount": 5, "spent_on": "2026-09-01"}
    )
    assert future.status_code == 422 and "future" in future.json()["detail"]
    assert unknown_category.status_code == 422
    assert "does not exist" in unknown_category.json()["detail"]


def test_expense_list_pagination_and_filters(api: TestClient) -> None:
    food, travel = create_category(api, "Food"), create_category(api, "Travel")
    for day in range(1, 6):
        create_expense(api, food["id"], spent_on=f"2026-09-0{day}")
    create_expense(api, travel["id"], spent_on="2026-08-31")

    page = api.get(f"{V1}/expenses", params={"page": 2, "page_size": 2}).json()
    assert (page["total"], page["page"], page["page_size"]) == (6, 2, 2)
    assert [e["spent_on"] for e in page["items"]] == ["2026-09-03", "2026-09-02"]

    filtered = api.get(
        f"{V1}/expenses",
        params={"category_id": food["id"], "date_from": "2026-09-04", "date_to": "2026-09-05"},
    ).json()
    assert filtered["total"] == 2

    assert api.get(f"{V1}/expenses", params={"page_size": 101}).status_code == 422
    bad_range = api.get(
        f"{V1}/expenses", params={"date_from": "2026-09-05", "date_to": "2026-09-01"}
    )
    assert bad_range.status_code == 422


# --- Audit trail ------------------------------------------------------------------


def test_every_change_is_audited_with_correlation_id(api: TestClient) -> None:
    headers = {"X-Correlation-ID": "cid-audit-test"}
    category = api.post(f"{V1}/categories", json={"name": "Food"}, headers=headers).json()
    expense = api.post(
        f"{V1}/expenses",
        json={"category_id": category["id"], "amount": 10, "spent_on": "2026-09-01"},
        headers=headers,
    ).json()
    api.put(
        f"{V1}/expenses/{expense['id']}",
        json={"category_id": category["id"], "amount": 12, "spent_on": "2026-09-01"},
        headers=headers,
    )
    api.delete(f"{V1}/expenses/{expense['id']}", headers=headers)
    api.put(f"{V1}/categories/{category['id']}", json={"name": "Groceries"}, headers=headers)
    api.delete(f"{V1}/categories/{category['id']}", headers=headers)

    entries = audit(api)
    assert [e["event_type"] for e in reversed(entries)] == [
        "CategoryCreated",
        "ExpenseCreated",
        "ExpenseUpdated",
        "ExpenseDeleted",
        "CategoryUpdated",
        "CategoryDeleted",
    ]
    assert {e["correlation_id"] for e in entries} == {"cid-audit-test"}
    updated = next(e for e in entries if e["event_type"] == "ExpenseUpdated")
    assert updated["payload"]["amount"] == "12.00"
    assert updated["occurred_at"].endswith("Z")

    expense_entries = audit(api, entity_type="expense", entity_id=expense["id"])
    assert len(expense_entries) == 3


def test_failed_change_writes_no_audit_row(api: TestClient) -> None:
    create_category(api, "Food")
    api.post(f"{V1}/categories", json={"name": "Food"})  # 409
    assert [e["event_type"] for e in audit(api)] == ["CategoryCreated"]


def test_audit_log_rejects_unknown_entity_type(api: TestClient) -> None:
    assert api.get(f"{V1}/audit-logs", params={"entity_type": "user"}).status_code == 422


# --- FX and OpenAPI ---------------------------------------------------------------


def test_fx_rates(api: TestClient) -> None:
    response = api.get(f"{V1}/fx/rates", params={"symbols": "usd, EUR,INR"})
    assert response.status_code == 200
    assert response.json() == {"base": "INR", "rates": {"USD": 0.012048, "EUR": 0.011111}}

    unknown = api.get(f"{V1}/fx/rates", params={"symbols": "USD,XYZ"})
    assert unknown.status_code == 422
    assert unknown.json()["detail"] == "Unsupported currencies: XYZ"


def test_openapi_money_fields_are_numbers(api: TestClient) -> None:
    schemas = api.get("/openapi.json").json()["components"]["schemas"]
    for model, field in [
        ("ExpenseCreate", "amount"),
        ("ExpenseRead", "amount_base"),
        ("ExpenseRead", "fx_rate"),
    ]:
        assert schemas[model]["properties"][field]["type"] == "number", (model, field)
    assert schemas["ExpenseCreate"]["properties"]["amount"]["multipleOf"] == 0.01
    budget = schemas["CategoryCreate"]["properties"]["monthly_budget"]["anyOf"][0]
    assert (budget["type"], budget["minimum"]) == ("number", 0)
