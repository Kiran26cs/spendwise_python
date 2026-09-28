"""``/api/v1/imports`` - upload a CSV of expenses; returns a per-row summary.

FX rates for all supported currencies are fetched up front (concurrently, cached), because
the synchronous importer cannot await. If the FX service is down, base-currency rows still
import and foreign rows fail individually.

Concepts: file uploads (``UploadFile``), stream ``seek``/``tell``, ``async`` + thread pool
bridging, graceful degradation with ``try/except``.
"""

import asyncio
import logging
import os
from decimal import Decimal
from functools import partial
from typing import BinaryIO

from fastapi import APIRouter, UploadFile, status
from starlette.concurrency import run_in_threadpool

from spendwise.api.deps import FxProviderDep, ImporterDep, SettingsDep
from spendwise.api.schemas import ImportResultRead, ProblemDetail
from spendwise.core.exceptions import ExternalServiceError, PayloadTooLargeError
from spendwise.domain.models import BASE_CURRENCY, Currency
from spendwise.infrastructure.fx_client import FxProvider

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/imports",
    tags=["imports"],
    responses={413: {"model": ProblemDetail}, 422: {"model": ProblemDetail}},
)


def _size_of(stream: BinaryIO) -> int:
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    return size


async def _rates_to_base(fx: FxProvider) -> dict[Currency, Decimal]:
    """Rate from each foreign currency to the base currency, fetched concurrently.

    A currency whose rate cannot be fetched is left out, so only its rows fail.
    """
    foreign = [currency for currency in Currency if currency != BASE_CURRENCY]
    results = await asyncio.gather(
        *(fx.get_rate(currency, BASE_CURRENCY) for currency in foreign), return_exceptions=True
    )
    rates: dict[Currency, Decimal] = {}
    for currency, result in zip(foreign, results, strict=True):
        if isinstance(result, ExternalServiceError):
            logger.warning("No FX rate for %s; its rows will fail", currency)
        elif isinstance(result, BaseException):
            raise result
        else:
            rates[currency] = result
    return rates


@router.post("/expenses", status_code=status.HTTP_200_OK)
async def import_expenses(
    file: UploadFile, importer: ImporterDep, fx: FxProviderDep, settings: SettingsDep
) -> ImportResultRead:
    """Import expenses from a UTF-8 CSV with columns
    ``spent_on,category,amount,currency,description``."""
    limit = settings.max_upload_mb * 1024 * 1024
    if _size_of(file.file) > limit:
        raise PayloadTooLargeError(f"File exceeds the {settings.max_upload_mb} MB limit")

    rates = await _rates_to_base(fx)
    result = await run_in_threadpool(partial(importer.import_csv, file.file, rates_to_base=rates))
    return ImportResultRead.model_validate(result)
