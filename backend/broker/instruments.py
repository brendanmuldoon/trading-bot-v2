"""Instrument metadata mapping (T09, §8.5 / C3).

Maps config universe symbols (SPY) to T212 tickers (SPY_US_EQ),
validates tradability in the account currency, and persists to the
`instruments` table. Untradable/unresolvable symbols are dropped with
a WARN event — never a crash. Re-run by the daily roll (T29).

Contract note: the OpenAPI bundle exposes no min-quantity field, so
min_qty persists as NULL = unknown (docs/t212-contract-notes.md).
"""

from decimal import Decimal
from typing import Any, Protocol

import httpx
from sqlalchemy.orm import Session, sessionmaker

from backend.broker.governor import Priority
from backend.events import log_event
from backend.models import EventLevel, Instrument

API = "/api/v0"

# Instrument types we will trade (no crypto/forex/warrants).
_EQUITY_TYPES = {"ETF", "STOCK"}


class Requester(Protocol):
    async def request(
        self,
        method: str,
        path: str,
        *,
        priority: Priority,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response: ...


async def sync_instruments(
    requester: Requester,
    session_factory: sessionmaker[Session],
    universe: list[str],
    account_currency: str,
) -> list[Instrument]:
    """Fetch metadata, resolve the universe, persist. Returns the
    tradable instruments. Callable at startup and from the daily roll."""
    resp = await requester.request(
        "GET", f"{API}/equity/metadata/instruments", priority=Priority.RECONCILE
    )
    metadata: list[dict[str, Any]] = resp.json()

    tradable: list[Instrument] = []
    with session_factory() as session:
        for symbol in universe:
            match = _resolve(metadata, symbol)
            if match is None:
                log_event(
                    EventLevel.WARNING,
                    "INSTRUMENT_UNRESOLVED",
                    f"{symbol}: no T212 instrument found; dropped from universe",
                    {"symbol": symbol},
                )
                _upsert(session, symbol, None, None, tradable=False)
                continue
            currency = match.get("currencyCode")
            if currency != account_currency:
                log_event(
                    EventLevel.WARNING,
                    "INSTRUMENT_WRONG_CURRENCY",
                    f"{symbol}: trades in {currency}, account is {account_currency}; dropped",
                    {"symbol": symbol, "currency": currency},
                )
                _upsert(session, symbol, match["ticker"], currency, tradable=False)
                continue
            row = _upsert(session, symbol, match["ticker"], currency, tradable=True)
            tradable.append(row)
        session.commit()
        for row in tradable:
            session.refresh(row)
            session.expunge(row)
    return tradable


def _resolve(metadata: list[dict[str, Any]], symbol: str) -> dict[str, Any] | None:
    candidates = [
        m
        for m in metadata
        if m.get("type") in _EQUITY_TYPES
        and (m.get("shortName") == symbol or str(m.get("ticker", "")).startswith(f"{symbol}_"))
    ]
    if not candidates:
        return None
    # Prefer US-listed equity tickers (SYMBOL_US_EQ) when several match.
    candidates.sort(key=lambda m: 0 if m.get("ticker") == f"{symbol}_US_EQ" else 1)
    return candidates[0]


def _upsert(
    session: Session,
    symbol: str,
    t212_ticker: str | None,
    currency: str | None,
    *,
    tradable: bool,
    min_qty: Decimal | None = None,
) -> Instrument:
    row = session.get(Instrument, symbol)
    if row is None:
        row = Instrument(symbol=symbol)
        session.add(row)
    row.t212_ticker = t212_ticker
    row.currency = currency
    row.tradable = tradable
    row.min_qty = min_qty
    return row
