"""All §11 application tables.

DBOS owns its separate system schema in the same database — not modeled
here. Tables marked [ui] in the spec are surfaced via REST + WebSocket.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.models.base import (
    Base,
    BotState,
    EventLevel,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionStatus,
    StopMode,
)


def _enum(enum_cls: type, name: str) -> Enum:
    """VARCHAR + CHECK constraint, values (not member names) stored."""
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda e: [m.value for m in e],
    )


class Instrument(Base):
    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    t212_ticker: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str | None] = mapped_column(String(8))
    tradable: Mapped[bool] = mapped_column(Boolean, default=False)
    min_qty: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))


class Candle(Base):
    __tablename__ = "candles"
    __table_args__ = (
        UniqueConstraint("symbol", "interval", "ts", name="uq_candles_symbol_interval_ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    o: Mapped[float] = mapped_column(Float, nullable=False)
    h: Mapped[float] = mapped_column(Float, nullable=False)
    l: Mapped[float] = mapped_column(Float, nullable=False)  # noqa: E741
    c: Mapped[float] = mapped_column(Float, nullable=False)
    v: Mapped[int] = mapped_column(BigInteger, nullable=False)


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    allocation: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_ref: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    t212_order_id: Mapped[str | None] = mapped_column(String(64), index=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[OrderSide] = mapped_column(_enum(OrderSide, "order_side"), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    type: Mapped[OrderType] = mapped_column(_enum(OrderType, "order_type"), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        _enum(OrderStatus, "order_status"), nullable=False, default=OrderStatus.PENDING_SUBMIT
    )
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    reason: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id"), nullable=False)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    avg_entry: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    stop_mode: Mapped[StopMode | None] = mapped_column(_enum(StopMode, "stop_mode"))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    status: Mapped[PositionStatus] = mapped_column(
        _enum(PositionStatus, "position_status"), nullable=False, default=PositionStatus.OPEN
    )


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    loop: Mapped[str] = mapped_column(String(16), nullable=False)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"))
    symbol: Mapped[str | None] = mapped_column(String(16))
    signal: Mapped[str | None] = mapped_column(String(16))
    action_taken: Mapped[str] = mapped_column(String(32), nullable=False)
    deny_reason: Mapped[str | None] = mapped_column(String(64))
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id"))
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    open_exposure: Mapped[float] = mapped_column(Float, nullable=False)
    drawdown: Mapped[float] = mapped_column(Float, nullable=False)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    level: Mapped[EventLevel] = mapped_column(_enum(EventLevel, "event_level"), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class BotStateRow(Base):
    """Singleton (§11): exactly one row, enforced by CHECK (id = 1)."""

    __tablename__ = "bot_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_bot_state_singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    state: Mapped[BotState] = mapped_column(
        _enum(BotState, "bot_state_enum"), nullable=False, default=BotState.UNCONFIGURED
    )
    halted_reason: Mapped[str | None] = mapped_column(Text)
    hwm_equity: Mapped[float | None] = mapped_column(Float)
    day_start_equity: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
