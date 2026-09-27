"""Structured logging: JSON formatter, correlation-ID filter and ``dictConfig`` setup.

Concepts: logging module (handlers, formatters, filters, ``dictConfig``), subclassing,
class attributes, set/dict comprehensions, ``json``, ``datetime`` with UTC.
"""

import json
import logging
import logging.config
from datetime import UTC, datetime
from typing import Any

from spendwise.core.config import Settings
from spendwise.core.context import correlation_id_var

# Attributes every LogRecord has; anything else was passed through ``extra=``.
_STANDARD_ATTRS = set(vars(logging.makeLogRecord({}))) | {"message", "asctime", "correlation_id"}


class CorrelationIdFilter(logging.Filter):
    """Copy the current request's correlation ID onto every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", correlation_id_var.get()),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        extras = {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}
        payload.update(extras)
        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    """Configure the root logger for JSON (prod) or human-readable (local) output."""
    formatter = "json" if settings.log_json else "text"
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {"correlation_id": {"()": CorrelationIdFilter}},
            "formatters": {
                "json": {"()": JsonFormatter},
                "text": {
                    "format": "%(asctime)s %(levelname)-8s [%(correlation_id)s] "
                    "%(name)s: %(message)s"
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": formatter,
                    "filters": ["correlation_id"],
                }
            },
            "root": {"level": settings.log_level, "handlers": ["console"]},
            "loggers": {"uvicorn.access": {"level": "WARNING"}},
        }
    )
