# E4 — Strategy Engine (T15–T20)

Spec refs: §6 (interface, registry, both strategies, hygiene), §13 (backtest harness starts here). Depends on E2 + E3.

---

## Task T15: Indicators with known-good-value tests

**Description:** SMA, RSI(14), Bollinger Bands (20, 2σ), and ATR(14) — either via a maintained TA library (`pandas-ta` if still maintained; resolve open question #3) or implemented directly. Either way, unit tests assert against independently computed known-good values on fixture series, including warm-up/NaN edges.

**Acceptance criteria:**
- [ ] All four indicators match reference values to tight tolerance on fixture data
- [ ] Warm-up periods (fewer bars than the window) return NaN/None, never garbage
- [ ] Pure functions over DataFrames — no I/O, no config reads

**Verification:**
- [ ] `pytest backend/tests/strategies/test_indicators.py`

**Dependencies:** T01

**Files likely touched:** `backend/strategies/indicators.py`, `backend/tests/strategies/test_indicators.py`, `backend/tests/strategies/fixtures/`

**Estimated scope:** S

---

## Task T16: Strategy interface, registry, and DB reconciliation

**Description:** The `Strategy` protocol (§6: `name`, `evaluate(candles, portfolio) -> list[Signal]`; `Signal = {ticker, action OPEN|CLOSE, reason, stop_price|None}`), a code-level registry (decorator or explicit list) where each class declares its name, typed param schema, and defaults, and the startup reconciliation with the `strategies` table (§6.0): missing rows inserted with seed allocation/params from config; existing rows win; `enabled=false` rows are loaded but never evaluated; the special `manual` ledger row is seeded too. Nothing anywhere may assume N = 2.

**Acceptance criteria:**
- [ ] First boot seeds `trend`, `meanrev`, `manual` rows from config; second boot with changed env does **not** overwrite DB values
- [ ] A registered-but-disabled strategy is skipped by the engine; an unregistered DB row logs a warning, doesn't crash
- [ ] Test registers a dummy third strategy and the engine evaluates all three with zero engine changes
- [ ] Params are validated against each strategy's typed schema on load

**Verification:**
- [ ] `pytest backend/tests/strategies/test_registry.py` — seeding rules, DB-wins, enabled flag, N=3 case (§13 unit list)

**Dependencies:** T02, T04

**Files likely touched:** `backend/strategies/base.py`, `backend/strategies/registry.py`, `backend/tests/strategies/test_registry.py`

**Estimated scope:** M

---

## Task T17: Trend-following strategy ("trend")

**Description:** §6.1 on hourly candles per ticker: eligible only when price > 200-SMA (regime filter); entry on fast-SMA(20) crossing above slow-SMA(50) on a completed bar with no open position in that ticker for this strategy; exit on the reverse cross. Stop price = entry − 2.0 × ATR(14) emitted with the OPEN signal (stop execution belongs to risk/stops layers). All parameters read from DB `params`. One position per ticker per strategy, no pyramiding.

**Acceptance criteria:**
- [ ] Fixture candle series produce the expected OPEN/CLOSE signals at exactly the crossover bars (no off-by-one)
- [ ] No OPEN when price ≤ 200-SMA, or when a position is already held in that ticker
- [ ] OPEN signals carry `stop_price = entry − 2.0 × ATR(14)`; params changed in DB change behavior without code edits

**Verification:**
- [ ] `pytest backend/tests/strategies/test_trend.py` — crossover fixtures incl. regime-filter and already-holding cases

**Dependencies:** T15, T16

**Files likely touched:** `backend/strategies/trend.py`, `backend/tests/strategies/test_trend.py`

**Estimated scope:** S

---

## Task T18: Mean-reversion strategy ("meanrev")

**Description:** §6.2 on hourly candles per ticker: regime filter (price > 200-SMA); entry when RSI(14) < 30 **and** close < lower Bollinger Band (20, 2σ) on a completed bar; exit when RSI > 55 **or** close ≥ middle band **or** time stop at 48 hourly bars held. Stop price = entry − 2.0 × ATR(14) on OPEN. All params from DB.

**Acceptance criteria:**
- [ ] Entry requires both RSI and band conditions; either alone produces no signal (fixture-tested)
- [ ] Each exit trigger (RSI, middle band, time stop) independently fires on its fixture
- [ ] Time stop counts held bars correctly across the fixture series

**Verification:**
- [ ] `pytest backend/tests/strategies/test_meanrev.py`

**Dependencies:** T15, T16

**Files likely touched:** `backend/strategies/meanrev.py`, `backend/tests/strategies/test_meanrev.py`

**Estimated scope:** S

---

## Task T19: Signal hygiene + evaluation engine

**Description:** The engine loop that the signal workflow (T27) will call: assemble cached candles per ticker, drop the in-progress bar (§6.3 — completed candles only), skip stale tickers with `DATA_STALE`, run every enabled strategy, and resolve conflicts: within one strategy CLOSE wins over OPEN for the same ticker; across strategies conflicting signals are both allowed (separate ledgers). Output: ordered signal list handed to the Risk Manager.

**Acceptance criteria:**
- [ ] In-progress bar never reaches a strategy (boundary-tested around bar close time)
- [ ] Within-strategy OPEN+CLOSE conflict on one ticker resolves to CLOSE
- [ ] Cross-strategy conflicts pass through untouched; stale tickers skipped per ticker, not per cycle

**Verification:**
- [ ] `pytest backend/tests/strategies/test_engine.py`

**Dependencies:** T14, T16, T17, T18

**Files likely touched:** `backend/strategies/engine.py`, `backend/tests/strategies/test_engine.py`

**Estimated scope:** S

---

## Task T20: Backtest harness skeleton

**Description:** Per §13 the harness starts as soon as the engine exists. R1 skeleton: replay cached candles bar-by-bar through the **same** strategy + engine code (no reimplementation), simulate fills at next-bar open, track per-strategy virtual portfolios, and emit a trade list + summary stats (trade count, win rate, total P&L). Cost modeling, risk-manager integration, and the out-of-sample split are completed in T42 — design the harness so the real Risk Manager (T21–T23) can be slotted in.

**Acceptance criteria:**
- [ ] CLI (`python -m backend.backtest --from --to`) replays the candle cache through the real strategy classes and prints a trade list + stats
- [ ] Bar-by-bar replay shows strategies only completed bars (no lookahead — verified by a fixture that would tempt lookahead)
- [ ] Harness accepts a pluggable sizing/approval hook (placeholder fixed-size now; Risk Manager later)

**Verification:**
- [ ] `pytest backend/tests/backtest/test_harness.py` — deterministic fixture replay produces the hand-checked trade list
- [ ] Manual: run against real cached SPY/QQQ candles, sanity-check output

**Dependencies:** T14, T19

**Files likely touched:** `backend/backtest/harness.py`, `backend/backtest/__main__.py`, `backend/tests/backtest/test_harness.py`

**Estimated scope:** M

---

## Checkpoint C (see `00-overview.md`)
Indicators verified, both strategies fixture-tested, N=3 proven, backtest skeleton replays real cached data.
