# E7 — Stop Orders (T31–T33)

Spec ref: §8.3. Prefer broker-side stops; fall back to bot-enforced. Depends on E2 + E5; runs in parallel with E6 (T28/T30 consume its hooks).

---

## Task T31: Stop-order capability detection

**Description:** On startup and once per daily roll (§8.3): determine whether the current environment supports `/orders/stop` — by placing/cancelling a far-away stop on a 1-share equivalent in demo, or honoring a `FORCE_BOT_SIDE_STOPS=true` config override. Persist the detected mode; expose it to the monitor workflow, order flow, `/api/status`, and the UI header ("Stops: broker-side ✅ / bot-side ⚠️"). Log the mode prominently on every detection.

**Acceptance criteria:**
- [ ] Fake-server accept → broker-side mode; reject → bot-side mode; `FORCE_BOT_SIDE_STOPS=true` short-circuits detection
- [ ] Probe stop is always cleaned up (cancelled), including on partial failure
- [ ] Mode persisted, surfaced via status, and re-evaluated at the daily roll

**Verification:**
- [ ] `pytest backend/tests/stops/test_capability.py` — accept, reject, override, probe-cleanup-on-error

**Dependencies:** T10, T29 (roll hook)

**Files likely touched:** `backend/stops/capability.py`, `backend/tests/stops/test_capability.py`

**Estimated scope:** S

---

## Task T32: Broker-side stop placement and verification

**Description:** In broker-side mode: immediately after every entry fill, place a `/orders/stop` at the strategy's stop price via the durable order-workflow machinery (T11), at protective priority. The monitor loop verifies each open position's stop order still exists every cycle and re-creates missing ones (re-creation failure → `CRITICAL` event per §8.2). Stop fills flow through reconciliation to close the ledger lot. Startup re-establishes missing stops (T30 hook).

**Acceptance criteria:**
- [ ] Fill → stop placed in the same flow; `positions.stop_mode='broker'` and stop order id recorded
- [ ] Monitor cycle detects a deleted stop and re-creates it; repeated failure escalates `CRITICAL`
- [ ] Stop fill closes the position/lot correctly through reconciliation
- [ ] Startup with a position lacking its stop → stop re-established before loops resume

**Verification:**
- [ ] `pytest backend/tests/stops/test_broker_side.py` against fake server

**Dependencies:** T11, T25, T31

**Files likely touched:** `backend/stops/broker_side.py`, `backend/workflows/monitor.py` (hook), `backend/tests/stops/test_broker_side.py`

**Estimated scope:** M

---

## Task T33: Bot-side stop fallback

**Description:** In bot-side mode: the monitor loop compares current price (quote, or T212 position P&L during outages) to each position's `stop_price`; on breach, submit a protective market sell through the order workflow at top priority. `positions.stop_mode='bot'`. README documentation of the sleeping-machine risk (§8.3.4) is written in T43.

**Acceptance criteria:**
- [ ] Price-at-or-below stop on a monitor cycle → exactly one market sell workflow spawned (no duplicate exits on subsequent cycles while pending)
- [ ] Breach detection works from the fallback price source during a data outage
- [ ] Exits enforced even when PAUSED or daily-loss-limited (protective actions never blocked)

**Verification:**
- [ ] `pytest backend/tests/stops/test_bot_side.py` — breach, no-duplicate, outage fallback, paused-state enforcement

**Dependencies:** T11, T28, T31

**Files likely touched:** `backend/stops/bot_side.py`, `backend/workflows/monitor.py` (hook), `backend/tests/stops/test_bot_side.py`

**Estimated scope:** S

---

## Checkpoint E (joint with E6 — see `00-overview.md`)
Both stop modes integration-tested; capability detection + daily re-detection working; startup re-establishes stops.
