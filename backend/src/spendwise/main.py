"""Application factory: wires settings, logging, middleware, error handlers and routers.

Concepts: Factory pattern (``create_app``), async context managers (``lifespan``),
``**kwargs`` unpacking, keyword-only args, RFC 9457 Problem Details error mapping.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from spendwise.api.routers import audit, categories, expenses, fx, health
from spendwise.core.config import Settings, get_settings
from spendwise.core.context import CORRELATION_ID_HEADER, correlation_id_var
from spendwise.core.exceptions import SpendWiseError
from spendwise.core.logging import configure_logging
from spendwise.core.middleware import CorrelationIdMiddleware
from spendwise.infrastructure.db import create_db_engine, create_session_factory
from spendwise.infrastructure.fx_client import FrankfurterFxProvider, FxProvider, StaticFxProvider
from spendwise.services.event_bus import create_event_bus

logger = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"
API_PREFIX = "/api/v1"


def _create_fx_provider(settings: Settings, http_client: httpx.AsyncClient) -> FxProvider:
    """Static rates in tests (no network); the live Frankfurter API otherwise."""
    if settings.environment == "test":
        return StaticFxProvider()
    return FrankfurterFxProvider(
        http_client, settings.fx_api_url, cache_ttl_seconds=settings.fx_cache_ttl_seconds
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared resources on startup and release them on shutdown."""
    settings: Settings = app.state.settings
    async with httpx.AsyncClient(
        timeout=settings.fx_timeout_seconds, follow_redirects=True
    ) as http_client:
        app.state.http_client = http_client
        app.state.fx_provider = _create_fx_provider(settings, http_client)
        logger.info("Application started", extra={"environment": settings.environment})
        yield
    app.state.engine.dispose()
    logger.info("Application stopped")


def _problem(
    request: Request, *, status: int, title: str, detail: str, **extra: Any
) -> JSONResponse:
    """Build an RFC 9457 Problem Details response carrying the correlation ID."""
    correlation_id = getattr(request.state, "correlation_id", correlation_id_var.get())
    body = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": request.url.path,
        "correlation_id": correlation_id,
        **extra,
    }
    return JSONResponse(
        jsonable_encoder(body),
        status_code=status,
        media_type=PROBLEM_JSON,
        headers={CORRELATION_ID_HEADER: correlation_id},
    )


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, SpendWiseError)
    return _problem(request, status=exc.status_code, title=exc.title, detail=exc.detail)


async def _handle_http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    title = {404: "Not Found", 405: "Method Not Allowed"}.get(exc.status_code, "HTTP Error")
    return _problem(request, status=exc.status_code, title=title, detail=str(exc.detail))


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return _problem(
        request,
        status=422,
        title="Unprocessable Content",
        detail="Request validation failed",
        errors=exc.errors(),
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error", exc_info=exc)
    return _problem(
        request,
        status=500,
        title="Internal Server Error",
        detail="An unexpected error occurred.",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build a configured FastAPI application.

    Args:
        settings: Settings to use; defaults to the cached environment settings.
    """
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
        exception_handlers={
            SpendWiseError: _handle_app_error,
            StarletteHTTPException: _handle_http_error,
            RequestValidationError: _handle_validation_error,
            Exception: _handle_unexpected_error,
        },
    )
    app.state.settings = settings
    app.state.engine = create_db_engine(settings.database_url)
    app.state.session_factory = create_session_factory(app.state.engine)
    app.state.event_bus = create_event_bus()

    # Middleware added last runs first: correlation ID wraps CORS and everything else.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[CORRELATION_ID_HEADER],
    )
    app.add_middleware(CorrelationIdMiddleware)

    app.include_router(health.router)
    for module in (categories, expenses, audit, fx):
        app.include_router(module.router, prefix=API_PREFIX)
    return app


app = create_app()
