"""Reusable cross-cutting decorators: execution timing and retry with backoff.

Concepts: decorators, ``functools.wraps``, decorator factories (closures), ``ParamSpec``,
introspection with ``inspect.iscoroutinefunction``, ``async``/``await``, keyword-only args.
"""

import asyncio
import functools
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)


def log_execution_time[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    """Log how long ``func`` took, at DEBUG level. Works for sync and async functions."""
    if inspect.iscoroutinefunction(func):
        async_func = cast(Callable[P, Awaitable[Any]], func)

        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            started = time.perf_counter()
            try:
                return await async_func(*args, **kwargs)
            finally:
                _log_duration(func, started)

        return cast(Callable[P, R], async_wrapper)

    @functools.wraps(func)
    def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        started = time.perf_counter()
        try:
            return func(*args, **kwargs)
        finally:
            _log_duration(func, started)

    return sync_wrapper


def _log_duration(func: Callable[..., Any], started: float) -> None:
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    logger.debug(
        "%s took %.2f ms",
        func.__qualname__,
        duration_ms,
        extra={"function_name": func.__qualname__, "duration_ms": duration_ms},
    )


def retry[**P, R](
    *,
    times: int = 3,
    delay_seconds: float = 0.5,
    exceptions: tuple[type[BaseException], ...] = (httpx.HTTPError,),
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Retry the decorated function up to ``times`` attempts with exponential backoff.

    Args:
        times: Total number of attempts (including the first).
        delay_seconds: Delay before the first retry; doubles on each subsequent retry.
        exceptions: Exception types that trigger a retry. Others propagate immediately.
    """
    if times < 1:
        raise ValueError("times must be >= 1")

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(func):
            async_func = cast(Callable[P, Awaitable[Any]], func)

            @functools.wraps(func)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                for attempt in range(1, times + 1):
                    try:
                        return await async_func(*args, **kwargs)
                    except exceptions as exc:
                        if attempt == times:
                            raise
                        await asyncio.sleep(_backoff(func, attempt, delay_seconds, exc))
                raise AssertionError("unreachable")  # pragma: no cover

            return cast(Callable[P, R], async_wrapper)

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            for attempt in range(1, times + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    if attempt == times:
                        raise
                    time.sleep(_backoff(func, attempt, delay_seconds, exc))
            raise AssertionError("unreachable")  # pragma: no cover

        return sync_wrapper

    return decorator


def _backoff(
    func: Callable[..., Any], attempt: int, delay_seconds: float, exc: BaseException
) -> float:
    """Log a retry warning and return how long to wait before the next attempt."""
    delay = delay_seconds * 2.0 ** (attempt - 1)
    logger.warning(
        "Retrying %s after attempt %d failed: %r (waiting %.2fs)",
        func.__qualname__,
        attempt,
        exc,
        delay,
    )
    return delay
