# E2 — Broker Client (T06–T12)

The only code that talks to T212 (`backend/broker/`). Spec refs: §2 (constraints C1–C12), §8. Runs in parallel with E3 after E1.

**Contract note:** before T07, re-download the OpenAPI bundle (`https://docs.trading212.com/_bundle/api.json`) and diff against §8.1 — the API is beta (open question #1 in `00-overview.md`).

---

## Task T06: Fake T212 test server

**Description:** A respx/httpx-mock based fake of every §8.1 endpoint, used by all broker tests and later integration tests. Must simulate: rate-limit headers (`x-ratelimit-*`), 429 responses, market-order fills and partial fills, rejected stop orders, cursor pagination via `nextPagePath` (C10), negative-quantity sells (C6), and the duplicate-order hazard (a re-POSTed order creates a second order, per C11). Stateful enough to track positions/orders across a test scenario.

**Acceptance criteria:**
- [ ] All §8.1 endpoints faked with realistic JSON shapes (taken from the OpenAPI bundle)
- [ ] Configurable scenarios: happy fill, partial fill, 429 burst, timeout-then-success, stop rejection, multi-page history
- [ ] Re-POSTing an identical order request creates a duplicate (so T11's verify logic has something real to catch)

**Verification:**
- [ ] `pytest backend/tests/broker/test_fake_server.py` — the fake's own behavior tests pass

**Dependencies:** T01 (can start immediately after scaffold; parallel with T02–T05)

**Files likely touched:** `backend/tests/broker/fake_t212.py`, `backend/tests/broker/test_fake_server.py`

**Estimated scope:** M

---

## Task T07: T212 HTTP client core — auth, environments, error mapping

**Description:** `broker/t212.py` foundation: httpx async client with HTTP Basic auth from `API_KEY:API_SECRET` (C4), base URL selected by `T212_ENV` (C1, default demo), timeouts, and a typed error hierarchy (auth failure, rate limited, validation, server error, timeout/ambiguous). No retry logic here — that belongs to the governor (T08). Account summary/cash endpoints included; record account currency at startup (C3).

**Acceptance criteria:**
- [ ] Demo vs live base URL purely config-driven; credentials never logged
- [ ] `get_account_summary()` / `get_account_cash()` return typed models incl. currency
- [ ] HTTP failures map to the typed error hierarchy; timeout/5xx during a POST surfaces as `AmbiguousResultError` (T11 depends on this distinction)

**Verification:**
- [ ] `pytest backend/tests/broker/test_client_core.py` against the fake server (auth header, env switch, error mapping)

**Dependencies:** T02, T06

**Files likely touched:** `backend/broker/t212.py`, `backend/broker/errors.py`, `backend/broker/models.py`, `backend/tests/broker/test_client_core.py`

**Estimated scope:** S

---

## Task T08: Rate-limit governor

**Description:** Global request budget manager all T212 calls pass through (§8.2, C5): single async queue (one in-flight + spacing), per-endpoint tracking of `x-ratelimit-remaining`/`-reset`, proactive throttling when remaining < 20% of limit, exponential backoff with jitter on 429 (max 5 retries), and priority ordering when budget is tight: protective exits > entries > reconciliation > history sync. A failed protective order escalates to a `CRITICAL` event and re-queues at top priority — never silently dropped.

**Acceptance criteria:**
- [ ] No two requests in flight simultaneously; header state stored per endpoint
- [ ] Simulated headers with remaining < 20% delay the next call to that endpoint until reset
- [ ] 429 → backoff with jitter, ≤ 5 retries, then cycle marked failed; protective-exit failure emits `CRITICAL` event and re-queues
- [ ] Priority queue ordering verified under contention

**Verification:**
- [ ] `pytest backend/tests/broker/test_governor.py` — simulated header sequences, 429 storms, priority contention (per §13 unit list)

**Dependencies:** T07

**Files likely touched:** `backend/broker/governor.py`, `backend/tests/broker/test_governor.py`

**Estimated scope:** M

---

## Task T09: Instrument metadata mapping and validation

**Description:** On startup, fetch `/equity/metadata/instruments`, map config universe symbols (e.g. `SPY`) to T212 tickers (e.g. `SPY_US_EQ`), validate tradability in the account currency (C3), capture `min_qty`/fractional support, and persist to the `instruments` table. Untradable symbols are dropped with a logged warning event (UI banner consumes it later).

**Acceptance criteria:**
- [ ] Universe symbols resolve to T212 tickers; unresolvable or wrong-currency symbols dropped with a `WARN` event, never crash
- [ ] `instruments` rows persisted with currency, tradable flag, min_qty
- [ ] Mapping is refreshed by the daily roll (hook point exposed; wiring in T29)

**Verification:**
- [ ] `pytest backend/tests/broker/test_instruments.py` — happy map, missing symbol, wrong-currency symbol

**Dependencies:** T04, T07, T08

**Files likely touched:** `backend/broker/instruments.py`, `backend/tests/broker/test_instruments.py`

**Estimated scope:** S

---

## Task T10: Positions and orders endpoints

**Description:** Client methods for `GET /equity/positions` (C9 — includes current P&L, usable as a quote fallback), `POST /equity/orders/market`, `POST /equity/orders/stop` (capability-gated by E7 but the client method ships now), `GET /equity/orders`, `GET /equity/orders/{id}`, `DELETE /equity/orders/{id}`. Encapsulate the negative-quantity sell convention (C6) behind a `side: BUY|SELL` parameter — the sign convention must never leak out of this module. Enforce the §C12 sanity cap on pending orders per ticker.

**Acceptance criteria:**
- [ ] Public API takes `side` + positive qty; the wire format uses signed quantity internally
- [ ] All order CRUD methods typed and tested incl. cancel
- [ ] Positions parse into typed models with quantity, avg price, P&L

**Verification:**
- [ ] `pytest backend/tests/broker/test_orders_positions.py` against fake server — buy, sell (asserts negative qty on the wire), cancel, position parsing

**Dependencies:** T07, T08

**Files likely touched:** `backend/broker/t212.py`, `backend/broker/models.py`, `backend/tests/broker/test_orders_positions.py`

**Estimated scope:** S

---

## Task T11: DBOS durable order workflow with verify-before-resubmit

**Description:** The per-order DBOS workflow (§8.4): Step 1 writes the `orders` row (`PENDING_SUBMIT`, locally generated `client_ref`); Step 2 POSTs to T212 and stores the T212 order id (`SUBMITTED`); Step 3 polls/reconciles to terminal status and updates the order row. **Recovery rule:** if the workflow recovers with Step 2 ambiguous (timeout/5xx/crash mid-call), it must not re-POST — it runs a verify step (fetch open orders + recent fills, match on ticker/qty/time window) and adopts the found order, resubmitting only if nothing matches. Partial fills: position qty follows actual fills; remainder cancelled after 2 monitor cycles (hook for T28). Protective workflows enqueue on a higher-priority DBOS queue than entries.

**Acceptance criteria:**
- [ ] Happy path: intent row → submit → confirm → `FILLED`, with each step checkpointed
- [ ] Durability: kill the process after Step 1 and separately mid-Step-2; on restart, recovery completes the order with **zero duplicate orders** against the fake server's duplicate-hazard mode (§13 durability test)
- [ ] Ambiguous submit (timeout but order actually placed) → verify step adopts it; ambiguous submit (order not placed) → safe resubmit
- [ ] Partial fill recorded correctly; remainder-cancel hook exposed

**Verification:**
- [ ] `pytest backend/tests/broker/test_order_workflow.py` — incl. subprocess kill/restart durability cases

**Dependencies:** T05, T06, T10

**Files likely touched:** `backend/workflows/order.py`, `backend/broker/verify.py`, `backend/tests/broker/test_order_workflow.py`

**Estimated scope:** M

---

## Task T12: History sync — paginated orders/transactions into Postgres

**Description:** Sync `GET /equity/history/orders` and `/history/transactions` into the DB, following `nextPagePath` cursors until null (C10, limit ≤ 50/page), idempotently upserting (re-running never duplicates rows). This is the lowest-priority traffic class in the governor (T08). Exposed as a callable; scheduled wiring (every 15 min + startup) lands in T29.

**Acceptance criteria:**
- [ ] Multi-page fixture fully ingested; re-run is a no-op (idempotent upsert)
- [ ] Runs at `history` priority in the governor; interleaved protective traffic preempts it
- [ ] Sync failures log an event and leave previously synced data intact

**Verification:**
- [ ] `pytest backend/tests/broker/test_history_sync.py` — pagination, idempotency, priority interleave

**Dependencies:** T04, T08, T10

**Files likely touched:** `backend/broker/history.py`, `backend/tests/broker/test_history_sync.py`

**Estimated scope:** S

---

## Checkpoint B (joint with E3 — see `00-overview.md`)
Broker suite green against fake server; kill-mid-order durability proven; candle cache working.
