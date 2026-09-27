"""Dependency-injection providers used with FastAPI ``Depends()``; tests override them.

Shared objects (engine, session factory, event bus, FX provider) are created once in
``create_app()``/``lifespan`` and stored on ``app.state``; per-request objects (Unit of
Work, services) are built here from them.

Concepts: Dependency Injection, ``Annotated`` dependency aliases, provider chaining.
"""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine

from spendwise.infrastructure.db import SessionFactory
from spendwise.infrastructure.fx_client import FxProvider
from spendwise.infrastructure.unit_of_work import AbstractUnitOfWork, SqlAlchemyUnitOfWork
from spendwise.services.category_service import CategoryService
from spendwise.services.event_bus import EventBus
from spendwise.services.expense_service import ExpenseService


def get_engine(request: Request) -> Engine:
    engine: Engine = request.app.state.engine
    return engine


def get_uow(request: Request) -> AbstractUnitOfWork:
    """A fresh Unit of Work per request, publishing events to the app's event bus."""
    session_factory: SessionFactory = request.app.state.session_factory
    event_bus: EventBus = request.app.state.event_bus
    return SqlAlchemyUnitOfWork(session_factory, publisher=event_bus)


def get_fx_provider(request: Request) -> FxProvider:
    provider: FxProvider = request.app.state.fx_provider
    return provider


UowDep = Annotated[AbstractUnitOfWork, Depends(get_uow)]


def get_category_service(uow: UowDep) -> CategoryService:
    return CategoryService(uow)


def get_expense_service(uow: UowDep) -> ExpenseService:
    return ExpenseService(uow)


CategoryServiceDep = Annotated[CategoryService, Depends(get_category_service)]
ExpenseServiceDep = Annotated[ExpenseService, Depends(get_expense_service)]
FxProviderDep = Annotated[FxProvider, Depends(get_fx_provider)]
