"""Candle cache (T14, §7 / §6.3).

Candles persist to the `candles` table (unique symbol+interval+ts);
each refresh fetches only the delta past the latest cached bar in ONE
batched provider call. Failure policy: provider outage → DATA_OUTAGE
event and re-raise (the signal cycle skips, no orders — T27); stale
ticker (last bar older than 2 intervals) → DATA_STALE event and the
ticker is omitted from the returned frames. The cache doubles as
backtest data (T20/T42) via get_frames, which never hits the provider.
"""

import math
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from backend.events import log_event
from backend.marketdata.provider import (
    CANDLE_COLUMNS,
    MarketDataError,
    MarketDataProvider,
)
from backend.models import Candle, EventLevel

_INTERVAL_SECONDS = {"1h": 3600, "1d": 86400}


class CandleCache:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        provider: MarketDataProvider,
        *,
        interval: str = "1h",
        seed_lookback_bars: int = 400,
        stale_after_intervals: int = 2,
    ) -> None:
        self._session_factory = session_factory
        self._provider = provider
        self.interval = interval
        self.seed_lookback_bars = seed_lookback_bars
        self.stale_after_intervals = stale_after_intervals

    # ---- write path -----------------------------------------------------------

    def refresh(self, symbols: list[str], *, now: datetime | None = None) -> dict[str, int]:
        """Fetch the delta for all symbols in one provider call and upsert.
        Raises MarketDataError after logging DATA_OUTAGE on provider failure."""
        now = now or datetime.now(UTC)
        latest = {symbol: self.latest_ts(symbol) for symbol in symbols}
        lookback = self._delta_lookback(latest.values(), now)

        try:
            frames = self._provider.get_candles(symbols, self.interval, lookback)
        except MarketDataError as exc:
            log_event(
                EventLevel.ERROR,
                "DATA_OUTAGE",
                f"market data provider failed; skipping cycle: {exc}",
                {"symbols": symbols},
            )
            raise

        written: dict[str, int] = {}
        with self._session_factory() as session:
            for symbol, frame in frames.items():
                fresh = frame
                cutoff = latest.get(symbol)
                if cutoff is not None:
                    fresh = frame[frame.index > pd.Timestamp(cutoff)]
                written[symbol] = self._upsert(session, symbol, fresh)
            session.commit()
        return written

    def _upsert(self, session: Session, symbol: str, frame: pd.DataFrame) -> int:
        if frame.empty:
            return 0
        index = pd.DatetimeIndex(frame.index)
        rows = [
            {
                "symbol": symbol,
                "interval": self.interval,
                "ts": ts.to_pydatetime(),
                "o": float(frame["open"].iloc[i]),
                "h": float(frame["high"].iloc[i]),
                "l": float(frame["low"].iloc[i]),
                "c": float(frame["close"].iloc[i]),
                "v": int(frame["volume"].iloc[i]),
            }
            for i, ts in enumerate(index)
        ]
        statement = (
            pg_insert(Candle)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_candles_symbol_interval_ts")
            .returning(Candle.id)
        )
        inserted = session.execute(statement).fetchall()
        return len(inserted)

    # ---- read path (strategies + backtest; never hits the provider) -----------

    def get_frames(
        self, symbols: list[str], lookback_bars: int, *, now: datetime | None = None
    ) -> dict[str, pd.DataFrame]:
        """Per-symbol canonical frames from cache. Stale symbols (§6.3:
        last bar older than `stale_after_intervals`) are omitted with a
        DATA_STALE event."""
        now = now or datetime.now(UTC)
        max_age = timedelta(seconds=_INTERVAL_SECONDS[self.interval] * self.stale_after_intervals)
        out: dict[str, pd.DataFrame] = {}
        with self._session_factory() as session:
            for symbol in symbols:
                candles = session.scalars(
                    select(Candle)
                    .where(Candle.symbol == symbol, Candle.interval == self.interval)
                    .order_by(Candle.ts.desc())
                    .limit(lookback_bars)
                ).all()
                if not candles:
                    self._log_stale(symbol, None)
                    continue
                newest = candles[0].ts
                if now - newest > max_age:
                    self._log_stale(symbol, newest)
                    continue
                out[symbol] = _to_frame(list(reversed(candles)))
        return out

    def latest_ts(self, symbol: str) -> datetime | None:
        with self._session_factory() as session:
            return session.scalar(
                select(Candle.ts)
                .where(Candle.symbol == symbol, Candle.interval == self.interval)
                .order_by(Candle.ts.desc())
                .limit(1)
            )

    # ---- helpers ---------------------------------------------------------------

    def _delta_lookback(self, latest_values: object, now: datetime) -> int:
        seconds = _INTERVAL_SECONDS[self.interval]
        needed = 0
        for latest in latest_values:  # type: ignore[attr-defined]
            if latest is None:
                return self.seed_lookback_bars
            gap_bars = math.ceil((now - latest).total_seconds() / seconds)
            needed = max(needed, gap_bars)
        return max(min(needed + 2, self.seed_lookback_bars), 3)

    def _log_stale(self, symbol: str, newest: datetime | None) -> None:
        log_event(
            EventLevel.WARNING,
            "DATA_STALE",
            f"{symbol}: cached candles stale (last={newest}); ticker skipped",
            {"symbol": symbol, "interval": self.interval},
        )


def _to_frame(candles: list[Candle]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "open": [c.o for c in candles],
            "high": [c.h for c in candles],
            "low": [c.l for c in candles],
            "close": [c.c for c in candles],
            "volume": [c.v for c in candles],
        },
        index=pd.DatetimeIndex([c.ts for c in candles], tz=UTC, name="ts"),
    )
    return frame[CANDLE_COLUMNS]
