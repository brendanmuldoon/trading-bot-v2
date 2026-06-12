# Implementation Plan: T212 Trading Bot (R1)

**Source spec:** `spec/trading-bot-specification.md` (v1.2, approved for task decomposition)
**Scope:** R1 only — local docker-compose, paper trading, no auth, no cloud. R2 roadmap items (§17) are explicitly out of scope.

## Overview

An automated paper-trading bot for liquid US ETFs via the Trading 212 demo API. N pluggable strategies (R1 ships trend-following + mean reversion) run against per-strategy virtual ledgers, evaluated hourly via DBOS durable workflows, with continuous risk monitoring, broker-side or bot-side stops, and a React UI for auditing fed by REST + a backend-owned WebSocket channel.

44 tasks across 10 epics (T01–T44). Every task is S or M sized. Checkpoints close out each phase.

## Architecture Decisions (from spec — not to be relitigated during implementation)

- **One order path:** Strategy signal → Risk Manager → DBOS order workflow → Broker Client → DB. No component places orders directly (§3).
- **DB is the audit log; T212 is truth for positions.** Reconcile from broker on disagreement, never trade to match the DB (§9.4).
- **Backend owns realtime:** FastAPI WebSocket publishes topic-tagged change events after writes; UI uses TanStack Query + WS invalidation, no polling (§3, §12).
- **DBOS Transact (self-hosted, no Conductor)** replaces cron: scheduled workflows for signal/monitor/sync/daily-roll, per-order durable workflows with verify-before-resubmit (§8.4, §10).
- **DB-authoritative strategy config:** `strategies.allocation`/`params` seeded from env on first boot, DB wins thereafter (§5, §6.0).
- **Strategies emit intents only** (`Signal`: ticker, OPEN/CLOSE, reason, stop_price); Risk Manager sizes and approves. No N=2 assumption anywhere (§6).
- **yfinance behind a `MarketDataProvider` interface**; candles cached to Postgres (§7).

## Dependency Graph

```
E1 Foundations (T01–T05)
    │
    ├────────────────┬─────────────────┐
    ▼                ▼                 │
E2 Broker client   E3 Market data     │   (E2 ∥ E3 — parallelizable)
   (T06–T12)         (T13–T14)        │
    │                │                │
    └───────┬────────┘                │
            ▼                         │
      E4 Strategy engine (T15–T20, backtest skeleton starts here)
            │
            ▼
      E5 Risk & ledgers (T21–T25)
            │
       ┌────┴────┐
       ▼         ▼
 E6 Workflows  E7 Stops        (E6 ∥ E7 after E5)
  (T26–T30)     (T31–T33)
       └────┬────┘
            ▼
      E8 Backend API (T34–T36)
            ▼
      E9 Frontend (T37–T41; pages T38–T41 ∥ after T37)
            ▼
      E10 Acceptance (T42–T44)
```

## Task Files

| File | Epic | Tasks |
|------|------|-------|
| `01-foundations.md` | E1 Foundations | T01–T05 |
| `02-broker-client.md` | E2 Broker client | T06–T12 |
| `03-market-data.md` | E3 Market data | T13–T14 |
| `04-strategy-engine.md` | E4 Strategy engine | T15–T20 |
| `05-risk-ledgers.md` | E5 Risk & ledgers | T21–T25 |
| `06-workflows.md` | E6 Workflows & scheduling | T26–T30 |
| `07-stops.md` | E7 Stop orders | T31–T33 |
| `08-backend-api.md` | E8 REST + WebSocket | T34–T36 |
| `09-frontend.md` | E9 React UI | T37–T41 |
| `10-acceptance.md` | E10 Backtest + acceptance + runbook | T42–T44 |

## Phase Checkpoints

### Checkpoint A — after E1 (T01–T05)
- [ ] `docker compose up` brings up `postgres` + `app`; `/api/status` healthcheck passes
- [ ] Alembic migrations apply cleanly to an empty DB; all §11 tables exist
- [ ] `make check` passes (ruff, mypy, eslint, prettier, pytest, vitest all wired)
- [ ] Pre-commit hooks incl. gitleaks installed and firing
- [ ] App starts in "unconfigured" mode without API keys

### Checkpoint B — after E2 + E3 (T06–T14)
- [ ] Full broker client test suite green against fake T212 server (fills, partial fills, 429s, pagination)
- [ ] Durability test: process killed mid-order-workflow recovers without duplicate orders
- [ ] Candles fetched, cached, and delta-refreshed; staleness/outage events logged

### Checkpoint C — after E4 (T15–T20)
- [ ] Indicators match known-good values; both strategies emit correct signals on fixture data
- [ ] Registry ↔ DB seeding/reconciliation tests pass; adding a dummy strategy #3 requires zero engine changes
- [ ] Backtest skeleton replays cached candles through the real strategy code and produces a trade list

### Checkpoint D — after E5 (T21–T25)
- [ ] Sizing, limits, breakers all unit-tested; FIFO ledger P&L matches hand-computed fixtures
- [ ] Reconciliation handles the shared-ticker (two strategies, one broker position) case and the manual-trade case

### Checkpoint E — after E6 + E7 (T26–T33)
- [ ] All four scheduled workflows run, gate on market hours, never overlap per type
- [ ] Startup sequence: DBOS recovery → reconcile → re-establish stops → resume loops (tested via kill/restart)
- [ ] Stop capability detection works; both broker-side and bot-side modes covered by integration tests
- [ ] End-to-end on demo account (or fake server): signal → risk → order → fill → stop → reconcile

### Checkpoint F — after E8 + E9 (T34–T41)
- [ ] Every [ui] table reachable over REST; WS message fires for every meaningful write (integration-tested)
- [ ] All four pages render live data; pause/resume + halt-acknowledge work end-to-end from the UI
- [ ] No polling anywhere in the frontend

### Checkpoint G — R1 complete (T42–T44)
- [ ] Backtest harness with modeled costs + documented out-of-sample split informs final seed parameters
- [ ] README/runbook covers everything in §15; `.env.example` documents every variable
- [ ] 4-week paper acceptance run started with monitoring checklist in place

## Parallelization

- **Safe to parallelize:** E2 ∥ E3 (after E1); E6 ∥ E7 (after E5); frontend pages T38–T41 (after T37); fake T212 server (T06) can start the moment T01 lands.
- **Must be sequential:** E1 migrations before anything DB-touching; T37 data layer before pages; order workflow (T11) after rate-limit governor (T08).
- **Needs contract-first coordination:** E8 REST/WS payload shapes should be drafted before T37 starts so frontend and backend can proceed in parallel.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| T212 API is beta; endpoints/limits may change | High | Re-fetch OpenAPI bundle at build time (T07); capability detection at runtime (T31); fake-server tests isolate us from drift |
| Stop-order support in demo/live uncertain (C7) | Med | T31 detects at startup + daily; bot-side fallback (T33) always available |
| yfinance throttling/delays | Med | Candle cache (T14) + outage policy (skip cycle, fall back to T212 P&L per C9) |
| Duplicate orders on retry (C11) | High | Verify-before-resubmit in DBOS order workflow (T11); durability test kills process mid-submit |
| Laptop sleep/wake is routine downtime | Med | Startup sequence treats recovery as the normal path (T30); reconcile-first design (T25) |
| DBOS scheduled-workflow semantics differ from assumptions | Med | T05 spike validates scheduling + recovery on PG16 before epics build on it |
| Hourly strategies trade rarely → hard to verify liveness | Low | Decisions log records every evaluation incl. "no action" (T27); backtest harness gives evidence early (T20) |

## Open Questions (resolve before/at the flagged task)

1. **T07:** Does the current T212 OpenAPI bundle still match §8.1 endpoint paths? (Spec mandates re-verification at implementation time.)
2. **T31:** Does the demo environment accept `/orders/stop`? Determines default stop mode; both paths must ship regardless.
3. **T15:** `pandas-ta` maintenance status — if abandoned, implement SMA/RSI/BB/ATR directly (spec allows either; tests against known values required both ways).
4. **T09:** Which universe symbols support fractional quantities? Affects sizing floor behavior (§9.1) — read from instrument metadata `min_qty`.
