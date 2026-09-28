"""Exact numeric types for the API contract: JSON numbers on the wire, ``Decimal`` in Python.

A JSON number is converted to ``Decimal`` via its shortest text form, so ``1250.50`` becomes
exactly ``Decimal("1250.5")``. Strings and booleans are rejected; more decimal places than
allowed are rejected rather than rounded. The explicit ``le`` bound matters: ``max_digits``
alone accepts ``10000000000.0`` because it normalises to ``1E+10`` (1 digit).
See IMPLEMENTATION_PLAN.md section 8.6.

Concepts: ``typing.Annotated`` metadata, Pydantic validators and serializers,
custom JSON Schema.
"""

from decimal import Decimal
from typing import Annotated

from pydantic import BeforeValidator, Field, PlainSerializer, WithJsonSchema

from spendwise.domain.models import MAX_FX_RATE, MAX_MONEY


def _reject_non_numbers(value: object) -> object:
    # bool is a subclass of int, and strings would bypass the numeric contract
    if isinstance(value, (bool, str)):
        raise ValueError("must be a JSON number")
    return value


Money = Annotated[
    Decimal,
    BeforeValidator(_reject_non_numbers),
    Field(gt=0, le=MAX_MONEY, max_digits=12, decimal_places=2),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema(
        {"type": "number", "multipleOf": 0.01, "minimum": 0.01, "maximum": 9999999999.99}
    ),
]
"""A positive amount with at most 2 decimal places."""

Budget = Annotated[
    Decimal,
    BeforeValidator(_reject_non_numbers),
    Field(ge=0, le=MAX_MONEY, max_digits=12, decimal_places=2),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number", "multipleOf": 0.01, "minimum": 0, "maximum": 9999999999.99}),
]
"""Like ``Money`` but zero is allowed (a monthly budget may be 0)."""

FxRate = Annotated[
    Decimal,
    BeforeValidator(_reject_non_numbers),
    Field(gt=0, le=MAX_FX_RATE, max_digits=18, decimal_places=6),
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number", "multipleOf": 0.000001, "exclusiveMinimum": 0}),
]
"""A positive exchange rate with at most 6 decimal places."""

DecimalOut = Annotated[
    Decimal,
    PlainSerializer(float, return_type=float, when_used="json"),
    WithJsonSchema({"type": "number"}),
]
"""Response-only computed number (totals, remaining budget, percentages); may be negative."""
