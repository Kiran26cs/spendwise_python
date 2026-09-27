"""Foreign-exchange rates (Strategy pattern): live Frankfurter API or fixed static rates.

Concepts: ``Protocol``, ``async``/``await``, ``asyncio.gather`` (concurrent requests),
decorators (``@retry``), ``raise ... from``, a small TTL cache class with an injectable
clock (``time.monotonic``), ``json`` with ``parse_float=Decimal`` (no float step).
"""

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol

import httpx

from spendwise.core.decorators import log_execution_time, retry
from spendwise.core.exceptions import ExternalServiceError
from spendwise.domain.models import RATE_QUANTUM, Currency

logger = logging.getLogger(__name__)


def round_rate(value: Decimal) -> Decimal:
    return value.quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)


class FxProvider(Protocol):
    async def get_rate(self, from_: Currency, to: Currency) -> Decimal:
        """How many units of ``to`` one unit of ``from_`` buys."""
        ...

    async def get_rates(
        self, base: Currency, symbols: Iterable[Currency]
    ) -> dict[Currency, Decimal]:
        """Rates for each symbol relative to ``base`` (1 base = rate x symbol)."""
        ...


async def _gather_rates(
    provider: FxProvider, base: Currency, symbols: Iterable[Currency]
) -> dict[Currency, Decimal]:
    unique = list(dict.fromkeys(symbols))  # de-duplicate, keep order
    rates = await asyncio.gather(*(provider.get_rate(base, symbol) for symbol in unique))
    return dict(zip(unique, rates, strict=True))


class _TtlCache[K, V]:
    """Minimal in-memory cache whose entries expire ``ttl_seconds`` after being set."""

    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: dict[K, tuple[float, V]] = {}

    def get(self, key: K) -> V | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock() >= expires_at:
            del self._entries[key]
            return None
        return value

    def set(self, key: K, value: V) -> None:
        self._entries[key] = (self._clock() + self.ttl_seconds, value)


class FrankfurterFxProvider:
    """Live rates from https://api.frankfurter.dev (free, no API key), cached per pair."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        cache_ttl_seconds: float = 3600,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")
        self._cache: _TtlCache[tuple[Currency, Currency], Decimal] = _TtlCache(
            cache_ttl_seconds, clock
        )

    @log_execution_time
    async def get_rate(self, from_: Currency, to: Currency) -> Decimal:
        if from_ == to:
            return Decimal(1)
        cached = self._cache.get((from_, to))
        if cached is not None:
            return cached
        try:
            rate = await self._fetch_rate(from_, to)
        except (httpx.HTTPError, KeyError, ValueError, ArithmeticError) as exc:
            logger.error("FX lookup %s->%s failed: %r", from_, to, exc)
            raise ExternalServiceError(f"Could not fetch exchange rate {from_}->{to}") from exc
        self._cache.set((from_, to), rate)
        return rate

    async def get_rates(
        self, base: Currency, symbols: Iterable[Currency]
    ) -> dict[Currency, Decimal]:
        return await _gather_rates(self, base, symbols)

    @retry(times=3, delay_seconds=0.5)
    async def _fetch_rate(self, from_: Currency, to: Currency) -> Decimal:
        response = await self.client.get(
            f"{self.base_url}/latest", params={"from": from_.value, "to": to.value}
        )
        response.raise_for_status()
        body = response.json(parse_float=Decimal)  # exact: never goes through float
        rate = round_rate(Decimal(body["rates"][to.value]))
        if rate <= 0:
            raise ValueError(f"Non-positive rate {rate}")
        return rate


# Default offline rates: value of 1 unit in INR.
DEFAULT_STATIC_RATES: dict[Currency, Decimal] = {
    Currency.USD: Decimal("83.00"),
    Currency.EUR: Decimal("90.00"),
    Currency.GBP: Decimal("105.00"),
    Currency.AED: Decimal("22.60"),
    Currency.SGD: Decimal("61.50"),
}


class StaticFxProvider:
    """Fixed rates for tests and offline use. ``rates_to_inr`` maps currency -> INR value."""

    def __init__(self, rates_to_inr: dict[Currency, Decimal] | None = None) -> None:
        self.rates_to_inr = {Currency.INR: Decimal(1), **(rates_to_inr or DEFAULT_STATIC_RATES)}

    async def get_rate(self, from_: Currency, to: Currency) -> Decimal:
        if from_ == to:
            return Decimal(1)
        try:
            return round_rate(self.rates_to_inr[from_] / self.rates_to_inr[to])
        except KeyError as exc:
            raise ExternalServiceError(f"No static rate for {exc.args[0]}") from exc

    async def get_rates(
        self, base: Currency, symbols: Iterable[Currency]
    ) -> dict[Currency, Decimal]:
        return await _gather_rates(self, base, symbols)
