# E3 — Market Data (T13–T14)

Spec ref: §7. T212 provides no candles (C8) — yfinance behind a swappable interface. Runs in parallel with E2 after E1.

---

## Task T13: MarketDataProvider interface + yfinance implementation

**Description:** Define `MarketDataProvider` (`get_candles(tickers, interval, lookback)`, `get_quote(tickers)`) and implement it with yfinance: batched hourly-candle fetch for the whole universe in one call per signal cycle, lightweight quote checks for the monitor loop. Strategies and risk code must depend only on the interface so a paid provider can replace it without touching them.

**Acceptance criteria:**
- [ ] Interface is provider-agnostic (plain DataFrames/typed quotes — no yfinance types leak out)
- [ ] One batched request per candle cycle for the full universe
- [ ] Provider errors raise a typed `MarketDataError`; no internal retries that could stall the signal cycle

**Verification:**
- [ ] `pytest backend/tests/marketdata/test_provider.py` — mocked yfinance responses, batching, error mapping
- [ ] Manual: one-off script fetches real hourly SPY candles successfully

**Dependencies:** T02

**Files likely touched:** `backend/marketdata/provider.py`, `backend/marketdata/yfinance_provider.py`, `backend/tests/marketdata/test_provider.py`

**Estimated scope:** S

---

## Task T14: Candle cache, delta fetch, staleness & outage policy

**Description:** Persist fetched candles to the `candles` table (unique symbol+interval+ts); each cycle fetches only the delta since the latest cached bar. Implement the §6.3/§7 failure policies: per-ticker staleness (last candle older than 2 intervals → skip ticker, log `DATA_STALE`) and whole-provider outage (skip the signal cycle entirely, log `DATA_OUTAGE` — monitor loop falls back to T212 position P&L per C9, wired in T28). The cache doubles as backtest data (T20/T42).

**Acceptance criteria:**
- [ ] Repeat cycles only request bars newer than the cache; upserts never violate the unique constraint
- [ ] Stale ticker skipped with `DATA_STALE` event; provider failure aborts evaluation with `DATA_OUTAGE` event and no orders
- [ ] A cache-read API serves strategies (and later the backtest harness) without hitting the provider

**Verification:**
- [ ] `pytest backend/tests/marketdata/test_cache.py` — delta logic, staleness boundary (exactly 2 intervals), outage path, upsert idempotency

**Dependencies:** T04, T13

**Files likely touched:** `backend/marketdata/cache.py`, `backend/tests/marketdata/test_cache.py`

**Estimated scope:** S

---

## Checkpoint B (joint with E2 — see `00-overview.md`)
