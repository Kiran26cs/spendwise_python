"""Liveness endpoint reporting app version and database connectivity.

Concepts: ``APIRouter``, ``Annotated`` dependencies, ``Literal`` types, Pydantic response models,
conditional expressions.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel
from sqlalchemy import Engine

from spendwise.api.deps import get_engine
from spendwise.core.config import Settings, get_settings
from spendwise.infrastructure.db import ping

router = APIRouter(tags=["health"])


class HealthRead(BaseModel):
    """Health check response."""

    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "unavailable"]


@router.get("/health", responses={503: {"model": HealthRead}})
def health(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> HealthRead:
    """Report service health; 503 when the database does not respond."""
    database_ok = ping(engine)
    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthRead(
        status="ok" if database_ok else "degraded",
        version=settings.app_version,
        database="ok" if database_ok else "unavailable",
    )
