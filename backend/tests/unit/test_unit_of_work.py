"""Unit of Work behaviour shared by all implementations (exercised through the fake)."""

import pytest

from spendwise.domain.events import CategoryDeleted, DomainEvent, ExpensesImported
from spendwise.infrastructure.unit_of_work import AbstractUnitOfWork
from tests.fakes import FakeUnitOfWork


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[DomainEvent] = []
        self.committed_at_publish: list[bool] = []

    def publish(self, event: DomainEvent, uow: AbstractUnitOfWork) -> None:
        assert isinstance(uow, FakeUnitOfWork)
        self.published.append(event)
        self.committed_at_publish.append(uow.committed)
        if isinstance(event, CategoryDeleted):  # handlers may record follow-up events
            uow.record(ExpensesImported(count=0))


def test_commit_publishes_events_in_order_before_committing() -> None:
    publisher = RecordingPublisher()
    uow = FakeUnitOfWork(publisher)
    first, second = ExpensesImported(count=1), ExpensesImported(count=2)

    with uow:
        uow.record(first)
        uow.record(second)
        uow.commit()

    assert publisher.published == [first, second]
    assert publisher.committed_at_publish == [False, False]
    assert uow.committed


def test_events_recorded_by_handlers_are_also_published() -> None:
    publisher = RecordingPublisher()
    uow = FakeUnitOfWork(publisher)

    with uow:
        uow.record(CategoryDeleted(category_id=1))
        uow.commit()

    assert [e.event_type for e in publisher.published] == ["CategoryDeleted", "ExpensesImported"]


def test_exit_without_commit_rolls_back_and_drops_events() -> None:
    publisher = RecordingPublisher()
    uow = FakeUnitOfWork(publisher)

    with uow:
        uow.record(ExpensesImported(count=1))

    assert uow.rollbacks == 1
    assert not uow.committed
    with uow:
        uow.commit()
    assert publisher.published == []


def test_exception_rolls_back_and_propagates() -> None:
    uow = FakeUnitOfWork()

    with pytest.raises(RuntimeError), uow:
        raise RuntimeError("boom")

    assert uow.rollbacks == 1
    assert not uow.committed


def test_commit_without_publisher() -> None:
    uow = FakeUnitOfWork()
    with uow:
        uow.record(ExpensesImported(count=1))
        uow.commit()
    assert uow.committed
