"""Liveness endpoint reporting app version and database connectivity.

Concepts: ``APIRouter``, ``Annotated`` dependencies, ``Literal`` types, Pydantic response models.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from spendwise.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


class HealthRead(BaseModel):
    """Health check response."""

    status: Literal["ok", "degraded"]
    version: str
    database: Literal["ok", "unavailable", "unchecked"]


@router.get("/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthRead:
    """Report service health. The database check is stubbed until Phase 3."""
    return HealthRead(status="ok", version=settings.app_version, database="unchecked")
