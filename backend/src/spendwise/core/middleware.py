"""ASGI middleware that assigns a correlation ID to each request and logs its lifecycle.

Concepts: ASGI protocol (scope/receive/send), closures, ``contextvars`` tokens,
``try/finally``, ``time.perf_counter``, ``uuid``.
"""

import logging
import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from spendwise.core.context import CORRELATION_ID_HEADER, correlation_id_var

logger = logging.getLogger(__name__)

_MAX_ID_LENGTH = 64


class CorrelationIdMiddleware:
    """Read ``X-Correlation-ID`` (or generate a UUID4), expose it via a ContextVar and header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(CORRELATION_ID_HEADER, "")
        correlation_id = incoming[:_MAX_ID_LENGTH] or str(uuid.uuid4())
        token = correlation_id_var.set(correlation_id)
        # Also kept on request.state: the 500 handler runs outside this middleware,
        # after the ContextVar has been reset.
        scope.setdefault("state", {})["correlation_id"] = correlation_id
        method, path = scope["method"], scope["path"]
        status_code = 500
        started = time.perf_counter()
        logger.info("request.start", extra={"method": method, "path": path})

        async def send_with_header(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[CORRELATION_ID_HEADER] = correlation_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logger.info(
                "request.end",
                extra={
                    "method": method,
                    "path": path,
                    "status": status_code,
                    "duration_ms": duration_ms,
                },
            )
            correlation_id_var.reset(token)
