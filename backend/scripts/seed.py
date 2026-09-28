"""Seed the database with sample categories and random expenses.

Usage (from ``backend/``)::

    uv run python scripts/seed.py --categories
    uv run python scripts/seed.py --expenses 50 --seed 42
    uv run python scripts/seed.py --reset --categories --expenses 100

Data goes through the normal services, so every change is audited like an API call.
Foreign-currency expenses use the offline static FX rates.

Concepts: ``argparse``, ``if __name__ == "__main__":``, ``random`` with a seed,
``asyncio.run`` from sync code, ``Decimal.scaleb``, ``timedelta``, exit codes.
"""

import argparse
import asyncio
import logging
import random
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete

from spendwise.core.config import get_settings
from spendwise.core.exceptions import ConflictError
from spendwise.core.logging import configure_logging
from spendwise.domain.models import BASE_CURRENCY, Currency
from spendwise.infrastructure.db import (
    AuditLogRow,
    CategoryRow,
    ExpenseRow,
    SessionFactory,
    create_db_engine,
    create_session_factory,
    session_scope,
)
from spendwise.infrastructure.fx_client import StaticFxProvider
from spendwise.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from spendwise.services.category_service import CategoryService
from spendwise.services.event_bus import create_event_bus
from spendwise.services.expense_service import ExpenseService

logger = logging.getLogger("spendwise.seed")

DEFAULT_CATEGORIES: dict[str, Decimal | None] = {
    "Groceries": Decimal("8000.00"),
    "Dining": Decimal("4000.00"),
    "Travel": Decimal("5000.00"),
    "Utilities": Decimal("3000.00"),
    "Entertainment": Decimal("2000.00"),
    "Health": None,
}
# Mostly base currency, occasionally foreign.
CURRENCY_WEIGHTS = {
    Currency.INR: 80,
    Currency.USD: 8,
    Currency.EUR: 5,
    Currency.GBP: 3,
    Currency.SGD: 2,
    Currency.AED: 2,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed SpendWise with sample data.")
    parser.add_argument("--categories", action="store_true", help="create the default categories")
    parser.add_argument(
        "--expenses", type=int, default=0, metavar="N", help="create N random expenses"
    )
    parser.add_argument("--days", type=int, default=90, help="spread expenses over the last N days")
    parser.add_argument("--reset", action="store_true", help="delete ALL existing data first")
    parser.add_argument("--seed", type=int, default=None, help="random seed (reproducible data)")
    args = parser.parse_args(argv)
    if not (args.categories or args.expenses or args.reset):
        parser.error("nothing to do: pass --categories, --expenses N and/or --reset")
    if args.expenses < 0 or args.days < 1:
        parser.error("--expenses must be >= 0 and --days >= 1")
    return args


def reset(session_factory: SessionFactory) -> None:
    with session_scope(session_factory) as session:
        for table in (AuditLogRow, ExpenseRow, CategoryRow):  # children before parents
            session.execute(delete(table))
    logger.info("Deleted all audit logs, expenses and categories")


def seed_categories(session_factory: SessionFactory) -> int:
    service = CategoryService(SqlAlchemyUnitOfWork(session_factory, create_event_bus()))
    created = 0
    for name, budget in DEFAULT_CATEGORIES.items():
        try:
            service.create(name=name, monthly_budget=budget)
            created += 1
        except ConflictError:
            logger.info("Category %s already exists, skipping", name)
    return created


def seed_expenses(
    session_factory: SessionFactory, count: int, days: int, rng: random.Random
) -> int:
    bus = create_event_bus()
    categories = CategoryService(SqlAlchemyUnitOfWork(session_factory, bus)).list()
    if not categories:
        raise SystemExit("No categories found: run with --categories first")
    expenses = ExpenseService(SqlAlchemyUnitOfWork(session_factory, bus))
    fx = StaticFxProvider()
    currencies, weights = list(CURRENCY_WEIGHTS), list(CURRENCY_WEIGHTS.values())
    today = date.today()

    for _ in range(count):
        currency = rng.choices(currencies, weights)[0]
        cents = (
            rng.randint(1_000, 500_000) if currency == BASE_CURRENCY else rng.randint(100, 20_000)
        )
        category = rng.choice(categories)
        assert category.id is not None
        expenses.create(
            category_id=category.id,
            amount=Decimal(cents).scaleb(-2),  # exact: 12345 -> 123.45
            currency=currency,
            fx_rate=asyncio.run(fx.get_rate(currency, BASE_CURRENCY)),
            description=f"Sample {category.name.lower()} expense",
            spent_on=today - timedelta(days=rng.randrange(days)),
        )
    return count


def run(args: argparse.Namespace, session_factory: SessionFactory) -> None:
    if args.reset:
        reset(session_factory)
    if args.categories:
        logger.info("Created %d categories", seed_categories(session_factory))
    if args.expenses:
        rng = random.Random(args.seed)
        created = seed_expenses(session_factory, args.expenses, args.days, rng)
        logger.info("Created %d expenses", created)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    engine = create_db_engine(settings.database_url)
    try:
        run(args, create_session_factory(engine))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
