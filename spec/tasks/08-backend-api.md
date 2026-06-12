# E8 — Backend API: REST + WebSocket (T34–T36)

Spec ref: §12. UI reads over REST, stays current via one topic-tagged WS channel. R1 has no auth, but the API layer must leave a single insertion point for an auth dependency (R2). Draft the payload shapes early so T37 can start in parallel.

---

## Task T34: REST read endpoints

**Description:** `GET /api/positions`, `/api/orders`, `/api/decisions`, `/api/events`, `/api/equity-snapshots`, `/api/strategies`, `/api/instruments` — the [ui] tables (§11), with pagination/filtering where it matters (orders/decisions/events by strategy, symbol, level, date range). Pydantic response models; CORS locked to the serving origin; one shared (currently no-op) auth dependency on every route.

**Acceptance criteria:**
- [ ] Every [ui] table readable with documented response schema (OpenAPI)
- [ ] Filters and cursor/offset pagination tested on orders, decisions, events
- [ ] All routes share a single auth dependency hook (no-op in R1)

**Verification:**
- [ ] `pytest backend/tests/api/test_reads.py` — per-endpoint shape, filter, and pagination tests

**Dependencies:** T04 (tables), T21 (snapshots exist)

**Files likely touched:** `backend/api/reads.py`, `backend/api/schemas.py`, `backend/api/deps.py`, `backend/tests/api/test_reads.py`

**Estimated scope:** M

---

## Task T35: Status, performance, config, and control endpoints

**Description:** `GET /api/status` (bot state, DEMO/LIVE badge, stop mode, data freshness, rate-limit headroom — doubles as the compose healthcheck); `GET /api/performance` (per-strategy derived stats: win rate, profit factor, average win/loss, max drawdown, exposure %, total P&L, trade count — computed from ledger data per §11); `GET /api/config` (sanitized — never returns secrets); `POST /api/control/pause` and `/api/control/resume` (resume from `HALTED` requires `{"acknowledge": true}`, else 409/422).

**Acceptance criteria:**
- [ ] Performance stats match hand-computed fixtures from a seeded trade history
- [ ] `/api/config` response provably contains no key/secret material (test asserts on the full payload)
- [ ] Pause/resume drive the T24 state machine; resume-from-HALTED without acknowledgment is rejected
- [ ] Status includes all five fields and works in UNCONFIGURED mode

**Verification:**
- [ ] `pytest backend/tests/api/test_status_control.py`

**Dependencies:** T21, T24, T31 (stop mode in status), T34

**Files likely touched:** `backend/api/status.py`, `backend/api/performance.py`, `backend/api/control.py`, `backend/tests/api/test_status_control.py`

**Estimated scope:** M

---

## Task T36: WebSocket event channel + publish-after-write

**Description:** `GET /api/ws` (SSE fallback acceptable): the backend publishes a topic-tagged message after every meaningful write — `{topic: "orders"|"positions"|"decisions"|"events"|"equity"|"bot_state"|"strategies", op, payload?}`. Small rows ship inline; larger changes send invalidation hints. Implement as a publish hook at the write layer (workflows, risk manager, reconciliation, control endpoints all flow through it) so no write path can forget to notify. Multi-client connection manager with disconnect cleanup.

**Acceptance criteria:**
- [ ] §13 realtime test: a backend write to each [ui] table produces the corresponding topic message to a connected client
- [ ] Topics and message schema exactly as specified; inline payload vs invalidation-hint rule applied
- [ ] Client disconnect/reconnect doesn't break the broadcaster; no messages while no writes (no polling/heartbeat-driven refetch)

**Verification:**
- [ ] `pytest backend/tests/api/test_ws.py` — async client integration test per topic

**Dependencies:** T05 (events helper), T34

**Files likely touched:** `backend/api/ws.py`, `backend/api/publish.py`, `backend/events.py` (publish hook), `backend/tests/api/test_ws.py`

**Estimated scope:** M

---

## Checkpoint F (joint with E9 — see `00-overview.md`)
