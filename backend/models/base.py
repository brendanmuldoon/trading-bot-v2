"""Declarative base and shared enums (spec §11).

Status enums are stored as VARCHAR + CHECK constraint (non-native enums)
so adding members later is a data-free migration.
"""

import enum

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class OrderSide(enum.StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(enum.StrEnum):
    MARKET = "MARKET"
    STOP = "STOP"
    LIMIT = "LIMIT"


class OrderStatus(enum.StrEnum):
    """Lifecycle per §8.4 plus T212 terminal statuses."""

    PENDING_SUBMIT = "PENDING_SUBMIT"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class PositionStatus(enum.StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class StopMode(enum.StrEnum):
    BROKER = "broker"
    BOT = "bot"


class BotState(enum.StrEnum):
    """§9.3: state persists across restarts."""

    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    HALTED = "HALTED"
    UNCONFIGURED = "UNCONFIGURED"


class EventLevel(enum.StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"
