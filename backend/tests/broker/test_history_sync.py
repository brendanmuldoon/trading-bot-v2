"""T12: pagination, idempotent upsert, priority class, failure isolation."""

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.broker.governor import Governor, Priority
from backend.broker.history import API, sync_history
from backend.broker.t212 import T212Client
from backend.db import get_sessionmaker
from backend.models import Order, OrderSide, OrderStatus, OrderType, Strategy, Transaction
from backend.tests.broker.fake_t212 import FakeT212


async def _instant_sleep(_seconds: float) -> None:
    return None


@pytest.fixture()
def fake() -> FakeT212:
    f = FakeT212(api_key="k", api_secret="s")
    f.add_instrument("SPY_US_EQ", price=500.0)
    f.seed_history_orders(12)
    f.seed_transactions(7)
    return f


@pytest.fixture()
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return get_sessionmaker(db_engine)


@pytest.fixture(autouse=True)
def clean_tables(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.query(Order).delete()
        session.query(Transaction).delete()
        session.commit()


@pytest.fixture()
def governor(fake: FakeT212) -> Iterator[Governor]:
    client = T212Client("k", "s", env="demo", transport=fake.transport)
    yield Governor(client, spacing=0.0, sleep=_instant_sleep)


@pytest.fixture(autouse=True)
def capture_events(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str]]]:
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "backend.broker.history.log_event",
        lambda level, code, message, context=None: captured.append((level.value, code)),
    )
    yield captured


class PriorityRecorder:
    """Wraps a governor, recording the priority of every request."""

    def __init__(self, inner: Governor) -> None:
        self._inner = inner
        self.priorities: list[Priority] = []

    async def request(
        self,
        method: str,
        path: str,
        *,
        priority: Priority,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        self.priorities.append(priority)
        return await self._inner.request(method, path, priority=priority, json=json, params=params)


async def test_multi_page_fully_ingested(
    governor: Governor, session_factory: sessionmaker[Session]
) -> None:
    counts = await sync_history(governor, session_factory)
    assert counts == {"orders": 12, "transactions": 7}
    with session_factory() as session:
        assert len(session.scalars(select(Order)).all()) == 12
        assert len(session.scalars(select(Transaction)).all()) == 7
        manual = session.scalar(select(Strategy).where(Strategy.name == "manual"))
        assert manual is not None and manual.enabled is False
        sample = session.scalars(select(Order)).first()
        assert sample is not None
        assert sample.fill_price is not None
        assert sample.status is OrderStatus.FILLED


async def test_rerun_is_idempotent(
    governor: Governor, session_factory: sessionmaker[Session]
) -> None:
    await sync_history(governor, session_factory)
    await sync_history(governor, session_factory)
    with session_factory() as session:
        assert len(session.scalars(select(Order)).all()) == 12
        assert len(session.scalars(select(Transaction)).all()) == 7


async def test_bot_originated_order_enriched_not_duplicated(
    governor: Governor, fake: FakeT212, session_factory: sessionmaker[Session]
) -> None:
    """A bot order already in the DB (by t212_order_id) gets its fill
    price updated instead of a second row."""
    with session_factory() as session:
        strategy = Strategy(name="trend-t12", allocation=0.5, params={})
        session.add(strategy)
        session.flush()
        session.add(
            Order(
                client_ref="bot-known",
                t212_order_id="1",  # matches the first seeded history order
                strategy_id=strategy.id,
                symbol="SPY_US_EQ",
                side=OrderSide.BUY,
                qty=Decimal("1"),
                type=OrderType.MARKET,
                status=OrderStatus.SUBMITTED,
                requested_at=datetime.now(UTC),
            )
        )
        session.commit()

    await sync_history(governor, session_factory)
    with session_factory() as session:
        rows = session.scalars(select(Order).where(Order.t212_order_id == "1")).all()
        assert len(rows) == 1
        assert rows[0].client_ref == "bot-known"  # enriched, not replaced
        assert rows[0].fill_price == Decimal("100.0")
        assert rows[0].status is OrderStatus.FILLED


async def test_runs_at_history_priority(
    governor: Governor, session_factory: sessionmaker[Session]
) -> None:
    recorder = PriorityRecorder(governor)
    await sync_history(recorder, session_factory)
    assert recorder.priorities, "no requests issued"
    assert set(recorder.priorities) == {Priority.HISTORY}


async def test_failure_logs_event_and_keeps_synced_data(
    governor: Governor,
    fake: FakeT212,
    session_factory: sessionmaker[Session],
    capture_events: list[tuple[str, str]],
) -> None:
    """Orders sync fine; transactions 500s → orders stay, event logged."""
    fake.queue_error(f"{API}/equity/history/transactions", 500, count=10)
    counts = await sync_history(governor, session_factory)
    assert counts["orders"] == 12
    assert counts["transactions"] == 0
    assert ("WARNING", "HISTORY_SYNC_FAILED") in capture_events
    with session_factory() as session:
        assert len(session.scalars(select(Order)).all()) == 12
