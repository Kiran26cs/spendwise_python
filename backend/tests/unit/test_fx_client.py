"""FX providers: Frankfurter over mocked HTTP (respx), TTL cache and static rates."""

import asyncio
from collections.abc import Coroutine, Iterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from spendwise.core.exceptions import ExternalServiceError
from spendwise.domain.models import Currency
from spendwise.infrastructure.fx_client import (
    FrankfurterFxProvider,
    StaticFxProvider,
    _TtlCache,
)

API = "https://fx.test"


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", instant)


@pytest.fixture
def mock_api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=API, assert_all_called=False) as router:
        yield router


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def rate_response(to: str, rate: str) -> httpx.Response:
    # Raw JSON text so the test proves the rate is parsed without a float step.
    body = f'{{"amount":1.0,"base":"USD","date":"2026-09-25","rates":{{"{to}":{rate}}}}}'
    return httpx.Response(200, text=body, headers={"content-type": "application/json"})


def run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


def make_provider(
    clock: FakeClock | None = None,
) -> tuple[FrankfurterFxProvider, httpx.AsyncClient]:
    client = httpx.AsyncClient()
    provider = FrankfurterFxProvider(
        client, API + "/", cache_ttl_seconds=60, clock=clock or FakeClock()
    )
    return provider, client


def test_parses_rate_exactly(mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/latest", params={"from": "USD", "to": "INR"}).mock(
        return_value=rate_response("INR", "95.8212345")
    )
    provider, _ = make_provider()

    rate = run(provider.get_rate(Currency.USD, Currency.INR))

    assert rate == Decimal("95.821235")  # rounded half up to 6 places, exact
    assert route.called


def test_same_currency_needs_no_request(mock_api: respx.MockRouter) -> None:
    provider, _ = make_provider()
    assert run(provider.get_rate(Currency.INR, Currency.INR)) == Decimal(1)
    assert not mock_api.calls


def test_rates_are_cached_until_ttl_expires(mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/latest").mock(return_value=rate_response("INR", "95.82"))
    clock = FakeClock()
    provider, _ = make_provider(clock)

    run(provider.get_rate(Currency.USD, Currency.INR))
    run(provider.get_rate(Currency.USD, Currency.INR))
    assert route.call_count == 1

    clock.now = 61
    run(provider.get_rate(Currency.USD, Currency.INR))
    assert route.call_count == 2


def test_retries_then_succeeds(mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/latest").mock(
        side_effect=[httpx.ConnectError("down"), rate_response("INR", "95.82")]
    )
    provider, _ = make_provider()

    assert run(provider.get_rate(Currency.USD, Currency.INR)) == Decimal("95.82")
    assert route.call_count == 2


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503),
        httpx.Response(200, json={"rates": {}}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"rates": {"INR": -1}}),
    ],
)
def test_failures_raise_external_service_error(
    mock_api: respx.MockRouter, response: httpx.Response
) -> None:
    mock_api.get("/latest").mock(return_value=response)
    provider, _ = make_provider()

    with pytest.raises(ExternalServiceError, match="USD->INR"):
        run(provider.get_rate(Currency.USD, Currency.INR))


def test_connection_errors_exhaust_retries(mock_api: respx.MockRouter) -> None:
    route = mock_api.get("/latest").mock(side_effect=httpx.ConnectError("down"))
    provider, _ = make_provider()

    with pytest.raises(ExternalServiceError):
        run(provider.get_rate(Currency.USD, Currency.INR))
    assert route.call_count == 3


def test_get_rates_fetches_concurrently(mock_api: respx.MockRouter) -> None:
    mock_api.get("/latest", params={"to": "USD"}).mock(return_value=rate_response("USD", "0.0104"))
    mock_api.get("/latest", params={"to": "EUR"}).mock(return_value=rate_response("EUR", "0.0091"))
    provider, _ = make_provider()

    async def fetch() -> dict[Currency, Decimal]:
        return await provider.get_rates(Currency.INR, [Currency.USD, Currency.EUR, Currency.USD])

    assert run(fetch()) == {Currency.USD: Decimal("0.0104"), Currency.EUR: Decimal("0.0091")}


def test_ttl_cache_expiry() -> None:
    clock = FakeClock()
    cache: _TtlCache[str, int] = _TtlCache(10, clock)
    assert cache.get("a") is None
    cache.set("a", 1)
    clock.now = 9.9
    assert cache.get("a") == 1
    clock.now = 10
    assert cache.get("a") is None


def test_static_provider_rates() -> None:
    fx = StaticFxProvider()

    async def fetch() -> tuple[Decimal, Decimal, Decimal, dict[Currency, Decimal]]:
        return (
            await fx.get_rate(Currency.USD, Currency.INR),
            await fx.get_rate(Currency.INR, Currency.USD),
            await fx.get_rate(Currency.EUR, Currency.EUR),
            await fx.get_rates(Currency.INR, [Currency.GBP]),
        )

    usd_inr, inr_usd, same, rates = run(fetch())
    assert usd_inr == Decimal("83.000000")
    assert inr_usd == Decimal("0.012048")
    assert same == Decimal(1)
    assert rates == {Currency.GBP: Decimal("0.009524")}


def test_static_provider_unknown_currency() -> None:
    fx = StaticFxProvider({Currency.USD: Decimal("83")})

    async def fetch() -> Decimal:
        return await fx.get_rate(Currency.EUR, Currency.INR)

    with pytest.raises(ExternalServiceError, match="EUR"):
        run(fetch())
