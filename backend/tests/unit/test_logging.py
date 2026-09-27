"""JSON formatter and correlation-ID filter."""

import json
import logging
import sys

from spendwise.core.context import correlation_id_var
from spendwise.core.logging import CorrelationIdFilter, JsonFormatter


def _record(msg: str = "hello %s", *args: object) -> logging.LogRecord:
    return logging.LogRecord("spendwise.test", logging.INFO, "mod.py", 42, msg, args, None)


def test_json_formatter_outputs_required_fields() -> None:
    token = correlation_id_var.set("cid-123")
    try:
        record = _record("hello %s", "world")
        CorrelationIdFilter().filter(record)
        output = json.loads(JsonFormatter().format(record))
    finally:
        correlation_id_var.reset(token)

    assert output["message"] == "hello world"
    assert output["level"] == "INFO"
    assert output["logger"] == "spendwise.test"
    assert output["correlation_id"] == "cid-123"
    assert output["line"] == 42
    assert {"timestamp", "module", "function"} <= output.keys()


def test_json_formatter_includes_extra_fields_and_exception() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        exc_info = sys.exc_info()
    record = logging.LogRecord("x", logging.ERROR, "m.py", 1, "failed", None, exc_info)
    record.duration_ms = 12.5

    output = json.loads(JsonFormatter().format(record))

    assert output["duration_ms"] == 12.5
    assert "ValueError: boom" in output["exc_info"]


def test_filter_uses_default_when_no_request() -> None:
    record = _record()
    CorrelationIdFilter().filter(record)
    assert vars(record)["correlation_id"] == "-"
