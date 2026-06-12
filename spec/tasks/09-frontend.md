# E9 — Frontend (T37–T41)

Spec ref: §12.1. React 18 + Vite + TS, TanStack Query + WS-driven invalidation, recharts, responsive. T37 first; the four pages (T38–T41) are parallelizable after it. No polling anywhere.

---

## Task T37: Data layer — TanStack Query + WebSocket invalidation

**Description:** Typed API client for every REST endpoint (T34/T35), TanStack Query hooks per resource, and a WS client for `/api/ws` that maps each topic-tagged message to cache invalidation (or direct cache patch when the payload ships inline). Reconnect with backoff; on reconnect, invalidate everything (missed messages). Shared layout shell with the status banner data (state, DEMO/LIVE badge, stop mode indicator) available app-wide. Vitest coverage for the message→invalidation mapping.

**Acceptance criteria:**
- [ ] One hook per resource; no `refetchInterval`/polling anywhere in the codebase (lint-guarded or grep-checked in CI target)
- [ ] Each WS topic invalidates/patches exactly its mapped queries (unit-tested with a mock socket)
- [ ] WS reconnect restores liveness and invalidates stale caches

**Verification:**
- [ ] `cd frontend && npx vitest run` — data-layer tests green
- [ ] Manual: with backend running, a DB write (e.g. pause) updates the UI without refresh

**Dependencies:** T34, T35, T36 (payload contracts; can start from drafted contracts earlier)

**Files likely touched:** `frontend/src/api/client.ts`, `frontend/src/api/hooks.ts`, `frontend/src/api/ws.ts`, `frontend/src/api/types.ts`, `frontend/src/App.tsx`, tests

**Estimated scope:** M

---

## Task T38: Dashboard page

**Description:** §12.1.1: state banner (RUNNING/PAUSED/HALTED + DEMO/LIVE badge + stop-mode indicator), total and per-strategy equity curves overlaid (recharts, from equity snapshots), open positions table, today's P&L, pause/resume button — resume-from-HALT behind a confirm dialog that sends `{"acknowledge": true}`.

**Acceptance criteria:**
- [ ] Banner reflects live bot state; pause/resume round-trips through the control API; halt-resume requires the confirm dialog
- [ ] Equity chart overlays total + one curve per enabled strategy (N curves, not 2)
- [ ] Open positions and today's P&L update live via WS (no refresh)

**Verification:**
- [ ] `npx vitest run` — component tests with mocked hooks (banner states, confirm dialog gating)
- [ ] Manual: pause from UI → banner flips; resume from simulated HALT requires confirmation

**Dependencies:** T37

**Files likely touched:** `frontend/src/pages/Dashboard.tsx`, `frontend/src/components/EquityChart.tsx`, `frontend/src/components/PositionsTable.tsx`, `frontend/src/components/StateBanner.tsx`, tests

**Estimated scope:** M

---

## Task T39: Trades page

**Description:** §12.1.2: filterable trade-history table (strategy, symbol, win/loss, date range) with pagination; a row expands to show the entry/exit orders, stop history, and the decision context (`decisions.context`) that opened the trade — the full audit chain for any position.

**Acceptance criteria:**
- [ ] All four filters work and combine; pagination against the REST API
- [ ] Row expansion shows linked orders, stop changes, and opening decision context
- [ ] New fills appear live via WS invalidation

**Verification:**
- [ ] `npx vitest run` — filter logic and expansion rendering with fixture data

**Dependencies:** T37

**Files likely touched:** `frontend/src/pages/Trades.tsx`, `frontend/src/components/TradeRow.tsx`, tests

**Estimated scope:** M

---

## Task T40: Strategy comparison page

**Description:** §12.1.3: side-by-side stat cards (win rate, profit factor, max DD, total P&L, trade count from `/api/performance`) and normalized equity curves (all starting at 100) for **every enabled strategy** — the layout must handle N strategies, not a fixed two. Per-symbol breakdown table per strategy. The `manual` ledger is excluded (it's not a strategy).

**Acceptance criteria:**
- [ ] Renders correctly with 1, 2, and 3+ enabled strategies (fixture-tested)
- [ ] Normalization: every curve starts at 100 regardless of allocation size
- [ ] Per-symbol breakdown matches the performance endpoint; `manual` never appears

**Verification:**
- [ ] `npx vitest run` — N-strategy layout and normalization math tests

**Dependencies:** T37

**Files likely touched:** `frontend/src/pages/Comparison.tsx`, `frontend/src/components/StatCard.tsx`, `frontend/src/components/NormalizedEquityChart.tsx`, tests

**Estimated scope:** M

---

## Task T41: Activity/Events page

**Description:** §12.1.4: decisions log (why the bot did or didn't trade — including denials and "no action" cycles) and system events, with level filters; data-freshness and rate-limit-headroom indicators from `/api/status`.

**Acceptance criteria:**
- [ ] Decisions and events streams filterable by level/strategy/code; live-updating
- [ ] Denial reasons and decision context legible (the "why didn't it trade" question answerable from this page alone)
- [ ] Freshness + rate-limit indicators reflect `/api/status` and degrade visibly during outages

**Verification:**
- [ ] `npx vitest run` — filter and indicator rendering tests

**Dependencies:** T37

**Files likely touched:** `frontend/src/pages/Activity.tsx`, `frontend/src/components/EventList.tsx`, `frontend/src/components/FreshnessIndicator.tsx`, tests

**Estimated scope:** S

---

## Checkpoint F (joint with E8 — see `00-overview.md`)
All four pages live-updating with no polling; pause/resume/halt-ack work end-to-end; UI built into the app image.
