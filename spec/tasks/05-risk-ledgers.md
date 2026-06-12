# E5 — Risk & Ledgers (T21–T25)

Spec ref: §9, §6.4. The Risk Manager is the single gatekeeper — every signal passes through it. Depends on E4.

---

## Task T21: Virtual ledgers and FIFO P&L

**Description:** Per-strategy virtual sub-portfolios (§6.4): buying power = `allocation × total bot equity − open exposure`; every order tagged with an immutable `strategy_id`; lots tracked FIFO **within each strategy** so two strategies holding the same ticker keep separate cost bases while the broker shows one combined position. Realized P&L computed per closed lot; win = realized P&L > 0 after estimated costs (§11). Equity snapshot writer (per strategy, per monitor cycle) included.

**Acceptance criteria:**
- [ ] FIFO realized P&L matches hand-computed fixtures incl. partial closes and multiple lots
- [ ] Two strategies holding the same ticker: ledger quantities sum to the combined broker quantity; P&L attributed per strategy
- [ ] Buying-power calculation reflects allocation, equity, and open exposure; snapshots write `equity, cash, open_exposure, drawdown` rows

**Verification:**
- [ ] `pytest backend/tests/risk/test_ledger.py` (§13: ledger/FIFO P&L unit tests)

**Dependencies:** T04, T16

**Files likely touched:** `backend/risk/ledger.py`, `backend/risk/snapshots.py`, `backend/tests/risk/test_ledger.py`

**Estimated scope:** M

---

## Task T22: Position sizing

**Description:** §9.1: `risk_amount = strategy_equity × RISK_PER_TRADE`; `quantity = risk_amount / (entry_price − stop_price)`; capped so position value ≤ 25% of strategy equity; respects the account-wide `CASH_BUFFER` (5% uncommitted). Fractional quantities where the instrument supports them (from `instruments.min_qty`), else floor to whole shares; resulting qty ≤ 0 → deny with reason `TOO_SMALL`.

**Acceptance criteria:**
- [ ] Sizing math matches hand-computed fixtures across fractional and whole-share instruments
- [ ] 25% cap and cash buffer each independently bind in dedicated test cases
- [ ] Zero/negative computed qty → `TOO_SMALL` denial, never a zero-qty order

**Verification:**
- [ ] `pytest backend/tests/risk/test_sizing.py`

**Dependencies:** T09 (instrument min_qty), T21

**Files likely touched:** `backend/risk/sizing.py`, `backend/tests/risk/test_sizing.py`

**Estimated scope:** S

---

## Task T23: Risk Manager — limits, breakers, decision recording

**Description:** The approve/deny/size gate (§9.2): max open positions per strategy (default 4); one position per ticker per strategy; daily loss limit (strategy's realized + unrealized today ≤ −3% of day-start equity → no new entries for that strategy until next trading day, exits unaffected); account drawdown halt (total equity ≥ 15% below high-water mark → `HALTED`, nothing closed automatically, human resume required); no entries in the first/last 15 minutes of the session. Every decision — approvals **and** denials with reason — is written to `decisions`.

**Acceptance criteria:**
- [ ] Each limit independently triggers in tests; denial reasons are specific (`MAX_POSITIONS`, `DAILY_LOSS`, `TOO_SMALL`, …)
- [ ] Daily-loss state is per-strategy and resets at the daily roll; drawdown halt is account-wide and sets `HALTED` with reason
- [ ] CLOSE signals are never blocked by entry-side limits
- [ ] Every evaluated signal produces a `decisions` row including denials

**Verification:**
- [ ] `pytest backend/tests/risk/test_risk_manager.py`

**Dependencies:** T21, T22

**Files likely touched:** `backend/risk/manager.py`, `backend/tests/risk/test_risk_manager.py`

**Estimated scope:** M

---

## Task T24: Bot state machine

**Description:** §9.3: `RUNNING` / `PAUSED` (human, via UI: no new entries, exits + stops still enforced) / `HALTED` (breaker: like paused but resume requires explicit acknowledgment) / `UNCONFIGURED` (no keys, scheduler off). Persisted in the `bot_state` singleton with `halted_reason`, `hwm_equity`, per-strategy day-start equity; survives restarts. Exposes guard methods the workflows and Risk Manager consult (`can_enter()`, `can_exit()`).

**Acceptance criteria:**
- [ ] Legal transitions enforced (e.g. `HALTED → RUNNING` only with acknowledgment); illegal ones rejected
- [ ] State and HWM survive process restart (DB-backed)
- [ ] PAUSED blocks entries but not exits/stops; UNCONFIGURED disables scheduling entirely

**Verification:**
- [ ] `pytest backend/tests/risk/test_bot_state.py` — transition table + persistence round-trip

**Dependencies:** T04

**Files likely touched:** `backend/risk/state.py`, `backend/tests/risk/test_bot_state.py`

**Estimated scope:** S

---

## Task T25: Reconciliation

**Description:** §9.4, run at startup and every monitor cycle: fetch T212 positions + open orders, compare with DB ledgers. Broker is truth for quantities; unexplained quantity is attributed to the `manual` ledger (excluded from strategy stats); log `RECONCILE_DIFF` and surface a UI warning event. Never place orders to "correct" the broker. Must handle the combined-position case: broker quantity for a ticker must equal the sum of all strategy ledgers' quantities. Startup ordering (after DBOS recovery, before loops resume) is wired in T30.

**Acceptance criteria:**
- [ ] Clean state → no-op; manual buy in T212 app → `manual` ledger entry + `RECONCILE_DIFF` event, strategy stats untouched
- [ ] Missed fill (broker shows position the DB lacks for a known order) → ledger adopted/corrected from broker
- [ ] Combined-ticker case: sum-of-ledgers vs broker mismatch detected and attributed correctly
- [ ] Reconciliation never emits orders

**Verification:**
- [ ] `pytest backend/tests/risk/test_reconcile.py` against fake-server scenarios

**Dependencies:** T10, T21, T24

**Files likely touched:** `backend/risk/reconcile.py`, `backend/tests/risk/test_reconcile.py`

**Estimated scope:** M

---

## Checkpoint D (see `00-overview.md`)
Sizing/limits/breakers unit-tested; FIFO fixtures pass; shared-ticker and manual-trade reconciliation proven.
