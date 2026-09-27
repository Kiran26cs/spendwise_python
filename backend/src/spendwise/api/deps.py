"""Dependency-injection providers used with FastAPI ``Depends()``; tests override them.

Concepts: Dependency Injection, reading shared state from ``request.app.state``.
"""

from fastapi import Request
from sqlalchemy import Engine


def get_engine(request: Request) -> Engine:
    engine: Engine = request.app.state.engine
    return engine
