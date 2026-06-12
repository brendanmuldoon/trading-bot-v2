"""History sync (T12, C10): paginated orders/transactions into Postgres.

Follows `nextPagePath` cursors until null (limit ≤ 50/page) and upserts
idempotently — re-running never duplicates rows. Runs at HISTORY
priority, the lowest traffic class in the governor, so protective/entry
traffic always preempts it. Failures log an event and leave previously
synced pages intact. Scheduled wiring (15 min + startup) lands in T29.
"""

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.broker.governor import Priority
from backend.broker.instruments import Requester
from backend.events import log_event
from backend.models import (
    EventLevel,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Strategy,
    Transaction,
)

API = "/api/v0"
PAGE_LIMIT = 50

logger = logging.getLogger("bot.broker.history")

_STATUS_MAP = {
    "FILLED": OrderStatus.FILLED,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "CANCELLING": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "REPLACED": OrderStatus.CANCELLED,
    "NEW": OrderStatus.SUBMITTED,
}


async def sync_history(
    requester: Requester, session_factory: sessionmaker[Session]
) -> dict[str, int]:
    """Sync both history feeds. Returns counts; never raises on broker
    failure — logs HISTORY_SYNC_FAILED instead (already-synced rows stay)."""
    counts = {"orders": 0, "transactions": 0}
    try:
        counts["orders"] = await _sync_orders(requester, session_factory)
        counts["transactions"] = await _sync_transactions(requester, session_factory)
    except Exception as exc:
        log_event(
            EventLevel.WARNING,
            "HISTORY_SYNC_FAILED",
            f"history sync aborted: {exc}",
            {"synced_so_far": counts},
        )
    return counts


async def _paginate(requester: Requester, first_path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    path: str | None = f"{first_path}?limit={PAGE_LIMIT}"
    while path:
        parsed = urlparse(path)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        resp = await requester.request("GET", parsed.path, priority=Priority.HISTORY, params=params)
        body = resp.json()
        items.extend(body.get("items", []))
        path = body.get("nextPagePath")
    return items


async def _sync_orders(requester: Requester, session_factory: sessionmaker[Session]) -> int:
    items = await _paginate(requester, f"{API}/equity/history/orders")
    written = 0
    with session_factory() as session:
        manual_id: int | None = None
        for item in items:
            payload = item.get("order") or {}
            fill = item.get("fill") or {}
            t212_id = payload.get("id")
            if t212_id is None:
                continue
            row = session.scalar(select(Order).where(Order.t212_order_id == str(t212_id)))
            if row is None:
                if manual_id is None:
                    manual_id = _manual_strategy_id(session)
                row = Order(
                    client_ref=f"hist-{t212_id}",
                    t212_order_id=str(t212_id),
                    strategy_id=manual_id,
                    symbol=payload.get("ticker", "?"),
                    side=OrderSide.SELL if payload.get("quantity", 0) < 0 else OrderSide.BUY,
                    qty=Decimal(str(abs(payload.get("quantity") or 0))),
                    type=OrderType.MARKET
                    if payload.get("type") == "MARKET"
                    else OrderType.STOP
                    if payload.get("type") == "STOP"
                    else OrderType.LIMIT,
                    status=OrderStatus.SUBMITTED,
                    requested_at=_parse_ts(payload.get("createdAt")),
                    reason="history sync (not bot-originated)",
                )
                session.add(row)
            status = _STATUS_MAP.get(payload.get("status", ""))
            if status is not None:
                row.status = status
            if fill.get("price") is not None:
                row.fill_price = Decimal(str(fill["price"]))
            if payload.get("filledQuantity") and row.filled_at is None:
                row.filled_at = _parse_ts(payload.get("createdAt"))
            row.raw_response = payload
            written += 1
        session.commit()
    return written


async def _sync_transactions(requester: Requester, session_factory: sessionmaker[Session]) -> int:
    items = await _paginate(requester, f"{API}/equity/history/transactions")
    written = 0
    with session_factory() as session:
        for item in items:
            reference = item.get("reference")
            if not reference:
                continue
            row = session.scalar(select(Transaction).where(Transaction.reference == reference))
            if row is None:
                row = Transaction(reference=reference)
                session.add(row)
            row.type = item.get("type", "UNKNOWN")
            row.amount = float(item.get("amount") or 0.0)
            row.currency = item.get("currency", "USD")
            row.occurred_at = _parse_ts(item.get("dateTime"))
            written += 1
        session.commit()
    return written


def _manual_strategy_id(session: Session) -> int:
    """The special `manual` ledger (§11) absorbs non-bot orders."""
    strategy = session.scalar(select(Strategy).where(Strategy.name == "manual"))
    if strategy is None:
        strategy = Strategy(name="manual", allocation=0.0, params={}, enabled=False)
        session.add(strategy)
        session.flush()
    return strategy.id


def _parse_ts(raw: str | None) -> datetime:
    if raw:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return datetime.now(UTC)
