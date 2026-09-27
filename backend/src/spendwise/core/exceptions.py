"""Application exception hierarchy. Each subclass maps to one HTTP status in ``main.py``.

Concepts: custom exceptions, inheritance, class attributes, ``super()``.
"""


class SpendWiseError(Exception):
    """Base class for all expected application errors."""

    status_code: int = 500
    title: str = "Internal Server Error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(SpendWiseError):
    """A requested entity does not exist."""

    status_code = 404
    title = "Not Found"


class DomainValidationError(SpendWiseError):
    """A business rule was violated."""

    status_code = 422
    title = "Unprocessable Content"


class ConflictError(SpendWiseError):
    """The request conflicts with the current state (duplicates, entity in use)."""

    status_code = 409
    title = "Conflict"


class ExternalServiceError(SpendWiseError):
    """A downstream service (e.g. the FX API) failed."""

    status_code = 502
    title = "Bad Gateway"
