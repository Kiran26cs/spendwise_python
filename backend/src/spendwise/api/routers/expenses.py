"""``/api/v1/expenses`` - FX rates are awaited here, then the sync service runs in a thread.

Concepts: ``async def`` endpoints, sync-to-async bridging with ``run_in_threadpool``,
``functools.partial``, query parameter validation, generic ``Page[T]`` responses.
"""

from datetime import date
from decimal import Decimal
from functools import partial
from typing import Annotated

from fastapi import APIRouter, Query, status
from starlette.concurrency import run_in_threadpool

from spendwise.api.deps import ExpenseServiceDep, FxProviderDep
from spendwise.api.schemas import ExpenseCreate, ExpenseRead, ExpenseUpdate, Page, ProblemDetail
from spendwise.domain.models import BASE_CURRENCY, Currency
from spendwise.infrastructure.fx_client import FxProvider

router = APIRouter(
    prefix="/expenses",
    tags=["expenses"],
    responses={
        404: {"model": ProblemDetail},
        422: {"model": ProblemDetail},
        502: {"model": ProblemDetail},
    },
)


async def _rate_to_base(fx: FxProvider, currency: Currency) -> Decimal:
    return await fx.get_rate(currency, BASE_CURRENCY)


@router.get("")
def list_expenses(
    service: ExpenseServiceDep,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    date_from: date | None = None,
    date_to: date | None = None,
    category_id: Annotated[int | None, Query(gt=0)] = None,
) -> Page[ExpenseRead]:
    items, total = service.list(
        page=page,
        page_size=page_size,
        date_from=date_from,
        date_to=date_to,
        category_id=category_id,
    )
    return Page[ExpenseRead](
        items=[ExpenseRead.model_validate(e) for e in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_expense(
    body: ExpenseCreate, service: ExpenseServiceDep, fx: FxProviderDep
) -> ExpenseRead:
    fx_rate = await _rate_to_base(fx, body.currency)
    create = partial(service.create, fx_rate=fx_rate, **body.model_dump())
    expense = await run_in_threadpool(create)
    return ExpenseRead.model_validate(expense)


@router.get("/{expense_id}")
def get_expense(expense_id: int, service: ExpenseServiceDep) -> ExpenseRead:
    return ExpenseRead.model_validate(service.get(expense_id))


@router.put("/{expense_id}")
async def update_expense(
    expense_id: int, body: ExpenseUpdate, service: ExpenseServiceDep, fx: FxProviderDep
) -> ExpenseRead:
    existing = await run_in_threadpool(service.get, expense_id)
    # Only a currency change needs a fresh rate; otherwise the stored rate is reused.
    fx_rate = None if body.currency == existing.currency else await _rate_to_base(fx, body.currency)
    update = partial(service.update, expense_id, fx_rate=fx_rate, **body.model_dump())
    expense = await run_in_threadpool(update)
    return ExpenseRead.model_validate(expense)


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(expense_id: int, service: ExpenseServiceDep) -> None:
    service.delete(expense_id)
