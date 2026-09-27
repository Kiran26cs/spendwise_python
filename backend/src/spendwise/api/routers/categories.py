"""``/api/v1/categories`` - thin HTTP adapter over ``CategoryService``.

Concepts: ``APIRouter``, path parameters, status codes, list comprehensions for DTO mapping.
Sync ``def`` endpoints run in FastAPI's thread pool (the DB layer is synchronous).
"""

from fastapi import APIRouter, status

from spendwise.api.deps import CategoryServiceDep
from spendwise.api.schemas import CategoryCreate, CategoryRead, CategoryUpdate, ProblemDetail

router = APIRouter(
    prefix="/categories",
    tags=["categories"],
    responses={
        404: {"model": ProblemDetail},
        409: {"model": ProblemDetail},
        422: {"model": ProblemDetail},
    },
)


@router.get("")
def list_categories(service: CategoryServiceDep) -> list[CategoryRead]:
    return [CategoryRead.model_validate(c) for c in service.list()]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_category(body: CategoryCreate, service: CategoryServiceDep) -> CategoryRead:
    category = service.create(name=body.name, monthly_budget=body.monthly_budget)
    return CategoryRead.model_validate(category)


@router.get("/{category_id}")
def get_category(category_id: int, service: CategoryServiceDep) -> CategoryRead:
    return CategoryRead.model_validate(service.get(category_id))


@router.put("/{category_id}")
def update_category(
    category_id: int, body: CategoryUpdate, service: CategoryServiceDep
) -> CategoryRead:
    category = service.update(category_id, name=body.name, monthly_budget=body.monthly_budget)
    return CategoryRead.model_validate(category)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int, service: CategoryServiceDep) -> None:
    service.delete(category_id)
