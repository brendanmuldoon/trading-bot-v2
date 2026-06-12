"""T04: round-trip insert/read per table plus DB-level constraint tests."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import (
    BotState,
    BotStateRow,
    Candle,
    Decision,
    EquitySnapshot,
    Event,
    EventLevel,
    Instrument,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionStatus,
    Strategy,
)

NOW = datetime(2026, 6, 12, 14, 0, tzinfo=UTC)


def make_strategy(db_session: Session, name: str = "trend") -> Strategy:
    strategy = Strategy(name=name, allocation=0.5, params={"fast_ma": 20}, enabled=True)
    db_session.add(strategy)
    db_session.flush()
    return strategy


def test_instrument_roundtrip(db_session: Session) -> None:
    db_session.add(
        Instrument(
            symbol="SPY",
            t212_ticker="SPY_US_EQ",
            currency="USD",
            tradable=True,
            min_qty=Decimal("0.01"),
        )
    )
    db_session.flush()
    row = db_session.get(Instrument, "SPY")
    assert row is not None
    assert row.t212_ticker == "SPY_US_EQ"
    assert row.min_qty == Decimal("0.01")


def test_candle_roundtrip_and_unique_constraint(db_session: Session) -> None:
    candle = Candle(symbol="QQQ", interval="1h", ts=NOW, o=1.0, h=2.0, l=0.5, c=1.5, v=1000)
    db_session.add(candle)
    db_session.flush()
    fetched = db_session.scalar(select(Candle).where(Candle.symbol == "QQQ"))
    assert fetched is not None and fetched.c == 1.5

    db_session.add(Candle(symbol="QQQ", interval="1h", ts=NOW, o=1, h=2, l=1, c=1, v=1))
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_strategy_roundtrip_params_json(db_session: Session) -> None:
    strategy = make_strategy(db_session, name="meanrev")
    fetched = db_session.get(Strategy, strategy.id)
    assert fetched is not None
    assert fetched.params == {"fast_ma": 20}


def test_order_roundtrip(db_session: Session) -> None:
    strategy = make_strategy(db_session)
    order = Order(
        client_ref="ref-001",
        strategy_id=strategy.id,
        symbol="SPY",
        side=OrderSide.BUY,
        qty=Decimal("1.5"),
        type=OrderType.MARKET,
        status=OrderStatus.PENDING_SUBMIT,
        requested_at=NOW,
        reason="ENTRY trend cross",
        raw_response={"id": 1},
    )
    db_session.add(order)
    db_session.flush()
    fetched = db_session.get(Order, order.id)
    assert fetched is not None
    assert fetched.side is OrderSide.BUY
    assert fetched.status is OrderStatus.PENDING_SUBMIT
    assert fetched.qty == Decimal("1.5")


def test_order_requires_valid_strategy_fk(db_session: Session) -> None:
    db_session.add(
        Order(
            client_ref="ref-bad-fk",
            strategy_id=999999,
            symbol="SPY",
            side=OrderSide.SELL,
            qty=Decimal("1"),
            type=OrderType.MARKET,
            status=OrderStatus.PENDING_SUBMIT,
            requested_at=NOW,
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_position_roundtrip(db_session: Session) -> None:
    strategy = make_strategy(db_session)
    position = Position(
        strategy_id=strategy.id,
        symbol="GLD",
        qty=Decimal("3"),
        avg_entry=Decimal("180.25"),
        stop_price=Decimal("175.00"),
        opened_at=NOW,
        status=PositionStatus.OPEN,
    )
    db_session.add(position)
    db_session.flush()
    fetched = db_session.get(Position, position.id)
    assert fetched is not None
    assert fetched.status is PositionStatus.OPEN
    assert fetched.stop_mode is None


def test_decision_roundtrip(db_session: Session) -> None:
    strategy = make_strategy(db_session)
    decision = Decision(
        ts=NOW,
        loop="signal",
        strategy_id=strategy.id,
        symbol="XLE",
        signal="OPEN",
        action_taken="DENIED",
        deny_reason="TOO_SMALL",
        context={"rsi": 28.4},
    )
    db_session.add(decision)
    db_session.flush()
    fetched = db_session.get(Decision, decision.id)
    assert fetched is not None
    assert fetched.deny_reason == "TOO_SMALL"


def test_equity_snapshot_roundtrip(db_session: Session) -> None:
    strategy = make_strategy(db_session)
    snap = EquitySnapshot(
        ts=NOW,
        strategy_id=strategy.id,
        equity=5000.0,
        cash=2500.0,
        open_exposure=0.5,
        drawdown=0.02,
    )
    db_session.add(snap)
    db_session.flush()
    fetched = db_session.get(EquitySnapshot, snap.id)
    assert fetched is not None
    assert fetched.drawdown == 0.02


def test_event_roundtrip(db_session: Session) -> None:
    event = Event(
        ts=NOW, level=EventLevel.WARNING, code="DATA_STALE", message="SPY stale", context={}
    )
    db_session.add(event)
    db_session.flush()
    fetched = db_session.get(Event, event.id)
    assert fetched is not None
    assert fetched.level is EventLevel.WARNING


def test_bot_state_singleton_enforced_at_db_level(db_session: Session) -> None:
    db_session.add(BotStateRow(id=1, state=BotState.RUNNING, hwm_equity=10000.0))
    db_session.flush()

    db_session.add(BotStateRow(id=2, state=BotState.PAUSED))
    with pytest.raises(IntegrityError):
        db_session.flush()
