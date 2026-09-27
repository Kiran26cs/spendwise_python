"""Request-scoped context shared across the call stack without passing arguments.

Concepts: ``contextvars.ContextVar`` (safe per-request state in async and threaded code).
"""

from contextvars import ContextVar

CORRELATION_ID_HEADER = "X-Correlation-ID"

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")
