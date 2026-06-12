# E6 — Workflows & Scheduling (T26–T30)

Spec ref: §10. The four DBOS scheduled workflows + market-hours gating + startup sequence. Depends on E5; runs in parallel with E7.

---

## Task T26: Market calendar gating

**Description:** NYSE calendar via `pandas-market-calendars` (§9.5), pinned to `America/New_York` regardless of host TZ: is-market-open, half-days, holidays, session open/close times, and the first/last-15-minute entry-exclusion windows (§9.2). Gating happens *inside* each workflow's first step (schedules stay simple); outside hours the monitor runs degraded "watch" mode at slow cadence.

**Acceptance criteria:**
- [ ] Correct open/closed answers for fixtures: normal day, weekend, holiday, half-day early close
- [ ] First/last-15-minute windows computed from the actual session times (incl. 13:00 ET close on half-days)
- [ ] All times ET; host timezone irrelevant (test with a non-US TZ env)

**Verification:**
- [ ] `pytest backend/tests/workflows/test_calendar.py` with frozen times

**Dependencies:** T02

**Files likely touched:** `backend/workflows/calendar.py`, `backend/tests/workflows/test_calendar.py`

**Estimated scope:** S

---

## Task T27: Signal workflow (hourly)

**Description:** DBOS scheduled workflow at HH:01 ET during market hours (§10): check calendar → exit early if closed → refresh candles (delta fetch, T14) → run evaluation engine (T19) → Risk Manager (T23) → spawn order workflows (T11) for approvals → persist every decision including "no action" and denials. Respects bot state (no entries when PAUSED/HALTED; CLOSE signals still flow when PAUSED).

**Acceptance criteria:**
- [ ] Integration test (fake T212 + canned candles): full signal → risk → order → DB-record path produces expected orders and decision rows
- [ ] Market-closed instant → early exit, one decision-cycle record, zero provider/broker calls beyond none
- [ ] `DATA_OUTAGE` from the provider → no orders, event logged, workflow completes cleanly
- [ ] A cycle with zero signals still writes "evaluated, no action" decisions (liveness evidence, §16.4)

**Verification:**
- [ ] `pytest backend/tests/workflows/test_signal_workflow.py`

**Dependencies:** T11, T14, T19, T23, T24, T26

**Files likely touched:** `backend/workflows/signal.py`, `backend/tests/workflows/test_signal_workflow.py`

**Estimated scope:** M

---

## Task T28: Monitor workflow (~45 s)

**Description:** DBOS scheduled workflow every `MONITOR_INTERVAL_SECONDS` during market hours, slow watch cadence otherwise (§10): update quotes/position P&L (fallback to T212 positions P&L during data outages, C9) → enforce bot-side stops when in that mode / verify broker-side stops exist (hooks consumed by E7) → check daily-loss and drawdown breakers → reconcile fills (T25) → cancel stale partial-fill remainders after 2 cycles (T11 hook) → write per-strategy equity snapshots (T21).

**Acceptance criteria:**
- [ ] Each responsibility covered by an integration case: breaker trips set state correctly; snapshots written every cycle; partial remainder cancelled on cycle 2
- [ ] Data outage → P&L from broker positions, stop checks still function
- [ ] Outside market hours: watch mode, no orders possible

**Verification:**
- [ ] `pytest backend/tests/workflows/test_monitor_workflow.py`

**Dependencies:** T13, T21, T23, T25, T26 (stop hooks finalized with E7)

**Files likely touched:** `backend/workflows/monitor.py`, `backend/tests/workflows/test_monitor_workflow.py`

**Estimated scope:** M

---

## Task T29: History-sync and daily-roll scheduled workflows + non-overlap guards

**Description:** History sync every 15 min + on startup (wraps T12). Daily roll at market open (§10): reset per-strategy daily-loss baselines, refresh instrument metadata (T09), re-detect stop capability (T31 hook), prune old logs/events. Enforce non-overlap per workflow type via DBOS queues with concurrency 1 — a new run skips if the previous is still in flight (applies to monitor and signal too).

**Acceptance criteria:**
- [ ] Daily roll resets day-start equity baselines and triggers the metadata/capability refresh hooks
- [ ] Non-overlap: a long-running monitor instance causes the next scheduled one to skip, not queue up (tested with an artificially slow run)
- [ ] History sync fires on startup and on its 15-min cadence

**Verification:**
- [ ] `pytest backend/tests/workflows/test_schedules.py` — roll behavior, overlap guard, startup sync

**Dependencies:** T09, T12, T23, T26

**Files likely touched:** `backend/workflows/history.py`, `backend/workflows/daily_roll.py`, `backend/workflows/queues.py`, `backend/tests/workflows/test_schedules.py`

**Estimated scope:** S

---

## Task T30: Startup sequence and restart durability

**Description:** The ordered boot path (§10, §9.4): DBOS recovers interrupted workflows (e.g. an order that died mid-submit) → reconciliation (T25) → re-establish missing stops (E7 hook) → mark healthy → scheduled workflows resume. This is the routine laptop sleep/wake path (§16.3), not an exceptional one. Includes the §13 durability test: kill the whole app mid-order, restart, assert recovery → reconcile → resume ordering with no duplicate orders and no loop starting before reconciliation finishes.

**Acceptance criteria:**
- [ ] Boot order is enforced and observable (events: `RECOVERY_DONE` → `RECONCILE_DONE` → `STOPS_VERIFIED` → `RESUMED`)
- [ ] Kill/restart integration test passes: interrupted order completed exactly once, scheduled workflows resume after reconcile
- [ ] UNCONFIGURED mode skips recovery/loops entirely; bot state (PAUSED/HALTED) is preserved across restart

**Verification:**
- [ ] `pytest backend/tests/workflows/test_startup.py` — subprocess kill/restart scenario

**Dependencies:** T05, T11, T24, T25, T27–T29 (E7's T32/T33 plug into the stop-re-establish step)

**Files likely touched:** `backend/workflows/startup.py`, `backend/main.py`, `backend/tests/workflows/test_startup.py`

**Estimated scope:** M

---

## Checkpoint E (joint with E7 — see `00-overview.md`)
