"""Category use cases: list, get, create, update, delete (with audit events).

Concepts: classes with injected dependencies, context managers (``with uow:``),
custom exceptions, keyword-only arguments, decorators.
"""

from decimal import Decimal

from spendwise.core.decorators import log_execution_time
from spendwise.core.exceptions import ConflictError, NotFoundError
from spendwise.domain.events import CategoryCreated, CategoryDeleted, CategoryUpdated
from spendwise.domain.models import Category
from spendwise.infrastructure.unit_of_work import AbstractUnitOfWork


class CategoryService:
    def __init__(self, uow: AbstractUnitOfWork) -> None:
        self.uow = uow

    def list(self) -> list[Category]:
        with self.uow:
            return self.uow.categories.list()

    def get(self, category_id: int) -> Category:
        with self.uow:
            return self._get_or_raise(category_id)

    @log_execution_time
    def create(self, *, name: str, monthly_budget: Decimal | None) -> Category:
        """Create a category; names are unique (case-insensitive)."""
        category = Category(id=None, name=name, monthly_budget=monthly_budget)
        with self.uow:
            self._ensure_name_free(category.name)
            created = self.uow.categories.add(category)
            assert created.id is not None
            self.uow.record(
                CategoryCreated(
                    category_id=created.id,
                    name=created.name,
                    monthly_budget=created.monthly_budget,
                )
            )
            self.uow.commit()
        return created

    @log_execution_time
    def update(self, category_id: int, *, name: str, monthly_budget: Decimal | None) -> Category:
        with self.uow:
            self._get_or_raise(category_id)
            changed = Category(id=category_id, name=name, monthly_budget=monthly_budget)
            self._ensure_name_free(changed.name, ignore_id=category_id)
            updated = self.uow.categories.update(changed)
            self.uow.record(
                CategoryUpdated(
                    category_id=category_id,
                    name=updated.name,
                    monthly_budget=updated.monthly_budget,
                )
            )
            self.uow.commit()
        return updated

    @log_execution_time
    def delete(self, category_id: int) -> None:
        """Delete a category; refused while it still has expenses."""
        with self.uow:
            category = self._get_or_raise(category_id)
            in_use = self.uow.expenses.count_for_category(category_id)
            if in_use:
                raise ConflictError(
                    f"Category '{category.name}' has {in_use} expense(s) and cannot be deleted"
                )
            self.uow.categories.delete(category_id)
            self.uow.record(CategoryDeleted(category_id=category_id))
            self.uow.commit()

    def _get_or_raise(self, category_id: int) -> Category:
        category = self.uow.categories.get(category_id)
        if category is None:
            raise NotFoundError(f"Category {category_id} not found")
        return category

    def _ensure_name_free(self, name: str, *, ignore_id: int | None = None) -> None:
        clash = self.uow.categories.get_by_name(name)
        if clash is not None and clash.id != ignore_id:
            raise ConflictError(f"Category '{clash.name}' already exists")
