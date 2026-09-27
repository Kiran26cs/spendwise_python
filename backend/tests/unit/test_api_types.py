"""The exact-money JSON contract (IMPLEMENTATION_PLAN.md section 8.6)."""

from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from spendwise.api.types import Budget, FxRate, Money


class Body(BaseModel):
    amount: Money
    budget: Budget | None = None
    rate: FxRate = Decimal(1)


def parse(fragment: str) -> Body:
    return Body.model_validate_json("{" + fragment + "}")


@pytest.mark.parametrize("raw", ["1250.50", "0.01", "9999999999.99", "0.3", "12.340", "1e2"])
def test_money_accepts_json_numbers_exactly(raw: str) -> None:
    body = parse(f'"amount": {raw}')
    assert body.amount == Decimal(raw)
    assert isinstance(body.amount, Decimal)
    # serialized back as a JSON number with the same value
    assert Decimal(body.model_dump_json().split(":")[1].split(",")[0]) == Decimal(raw)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("12.345", "decimal places"),
        ('"12.50"', "must be a JSON number"),
        ("true", "must be a JSON number"),
        ("0", "greater than 0"),
        ("-5", "greater than 0"),
        ("10000000000.00", "less than or equal to"),
        ("0.30000000000000004", "digits"),
        ("null", "decimal"),
    ],
)
def test_money_rejects_invalid_values(raw: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        parse(f'"amount": {raw}')


@pytest.mark.parametrize(
    ("raw", "expected"), [("0", Decimal(0)), ("null", None), ("10.5", Decimal("10.5"))]
)
def test_budget_allows_zero_and_null(raw: str, expected: Decimal | None) -> None:
    assert parse(f'"amount": 1, "budget": {raw}').budget == expected


def test_budget_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        parse('"amount": 1, "budget": -0.01')


@pytest.mark.parametrize(("raw", "ok"), [("83.123456", True), ("83.1234567", False), ("0", False)])
def test_fx_rate_precision(raw: str, ok: bool) -> None:
    if ok:
        assert parse(f'"amount": 1, "rate": {raw}').rate == Decimal(raw)
    else:
        with pytest.raises(ValidationError):
            parse(f'"amount": 1, "rate": {raw}')


def test_python_mode_rejects_strings_too() -> None:
    with pytest.raises(ValidationError, match="must be a JSON number"):
        Body(amount="12.50")
