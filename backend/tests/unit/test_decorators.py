"""``@retry`` and ``@log_execution_time`` on sync and async functions."""

import asyncio
import logging

import httpx
import pytest

from spendwise.core.decorators import log_execution_time, retry


def test_retry_sync_retries_then_raises(caplog: pytest.LogCaptureFixture) -> None:
    calls = 0

    @retry(times=3, delay_seconds=0)
    def flaky() -> None:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("down")

    with pytest.raises(httpx.ConnectError):
        flaky()

    assert calls == 3
    assert sum("Retrying" in r.message for r in caplog.records) == 2


def test_retry_sync_succeeds_after_failure() -> None:
    calls = 0

    @retry(times=3, delay_seconds=0)
    def flaky() -> str:
        nonlocal calls
        calls += 1
        if calls < 2:
            raise httpx.ConnectError("down")
        return "ok"

    assert flaky() == "ok"
    assert calls == 2


def test_retry_async_retries_then_raises() -> None:
    calls = 0

    @retry(times=2, delay_seconds=0)
    async def flaky() -> None:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow")

    with pytest.raises(httpx.ReadTimeout):
        asyncio.run(flaky())

    assert calls == 2


def test_retry_does_not_catch_other_exceptions() -> None:
    calls = 0

    @retry(times=3, delay_seconds=0)
    def broken() -> None:
        nonlocal calls
        calls += 1
        raise KeyError("not retried")

    with pytest.raises(KeyError):
        broken()

    assert calls == 1


def test_retry_rejects_invalid_times() -> None:
    with pytest.raises(ValueError):
        retry(times=0)


def test_log_execution_time_sync(caplog: pytest.LogCaptureFixture) -> None:
    @log_execution_time
    def add(a: int, b: int) -> int:
        return a + b

    with caplog.at_level(logging.DEBUG, logger="spendwise.core.decorators"):
        assert add(2, 3) == 5

    assert add.__name__ == "add"
    assert any("took" in r.message for r in caplog.records)


def test_log_execution_time_async(caplog: pytest.LogCaptureFixture) -> None:
    @log_execution_time
    async def double(x: int) -> int:
        return x * 2

    with caplog.at_level(logging.DEBUG, logger="spendwise.core.decorators"):
        assert asyncio.run(double(4)) == 8

    assert any("double" in r.message and "took" in r.message for r in caplog.records)
