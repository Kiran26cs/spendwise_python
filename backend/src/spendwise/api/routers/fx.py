"""``/api/v1/fx/rates`` - current rates relative to the base currency, fetched concurrently.

Concepts: ``async def`` endpoints, parsing comma-separated query values, set comprehensions.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from spendwise.api.deps import FxProviderDep
from spendwise.api.schemas import FxRatesRead, ProblemDetail
from spendwise.core.exceptions import DomainValidationError
from spendwise.domain.models import BASE_CURRENCY, Currency

router = APIRouter(
    prefix="/fx",
    tags=["fx"],
    responses={422: {"model": ProblemDetail}, 502: {"model": ProblemDetail}},
)


def _parse_symbols(raw: str) -> list[Currency]:
    codes = [code.strip().upper() for code in raw.split(",") if code.strip()]
    unknown = sorted({code for code in codes if code not in Currency.__members__})
    if unknown:
        raise DomainValidationError(f"Unsupported currencies: {', '.join(unknown)}")
    return [Currency(code) for code in codes if code != BASE_CURRENCY]


@router.get("/rates")
async def get_rates(
    fx: FxProviderDep,
    symbols: Annotated[str, Query(examples=["USD,EUR"])] = "USD,EUR,GBP,AED,SGD",
) -> FxRatesRead:
    """Rates as "1 INR = rate x symbol"."""
    rates = await fx.get_rates(BASE_CURRENCY, _parse_symbols(symbols))
    return FxRatesRead(base=BASE_CURRENCY, rates=rates)
