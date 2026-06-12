# Specification: Trading 212 Automated Trading Bot ("T212 Bot")

**Version:** 1.2 — June 2026 (revised after intent interview: R1 is local-first; ElectricSQL/TanStack DB removed in favour of backend-owned WebSocket realtime; strategies generalized from a fixed pair to a pluggable N-strategy registry with DB-stored allocations; Supabase, hosting, and CI/CD deferred to the R2 roadmap, §17)
**Status:** Approved for task decomposition
**Audience:** This document is written for an agent/engineer who will break it into implementation sub-tasks. Each numbered section is intended to be decomposable. Section 14 proposes an epic/milestone breakdown.

---

## 1. Overview & Goals

An automated trading system that:

1. Trades **large, liquid US ETFs** via the **Trading 212 Public API**, starting in the **paper (demo) environment** and later switchable to live via configuration only.
2. Runs **N strategies in parallel** as a pluggable registry — R1 ships two (trend-following and mean reversion) — each allocated a **fraction of capital as a virtual ledger** (stored in the DB, seeded 50/50), so their performance can be compared head-to-head. Adding a strategy is a code-level act in R1 (new class + restart); UI-level strategy management is roadmap (§17).
3. Evaluates signals **hourly** during US market hours; enforces risk continuously via a faster monitor loop and (where supported) broker-side stop orders.
4. Provides a **React web UI** for trade auditing: live positions, full trade history, per-trade P&L, win/loss stats, equity curves, and strategy comparison. The UI is a product the owner lives in, not just an audit view.
5. Is **fully automatic** (no human approval per trade), with a **pause/resume control** and automatic circuit breakers.
6. Is **GitHub-ready**: no secrets in the repo, everything configurable via environment variables, Dockerized.

### Release plan
- **R1 (this spec):** runs **locally on the owner's machine** via docker-compose (app + Postgres), paper trading only, no auth, no cloud services. While the machine is asleep or off, the bot does not trade and bot-side stops are not enforced — acceptable on demo money, recorded as a risk (§16).
- **R2+ (roadmap, §17):** hosted backend (Railway or similar), Supabase as managed Postgres + Auth, CI/CD, UI-level strategy management, live-trading promotion.

### Non-goals (v1)
- No short selling, options, CFDs, or leverage (the T212 Public API only supports Invest/ISA accounts — long-only equities/ETFs).
- No machine learning / model training.
- No multi-user support; single owner, single T212 account.
- No mobile app (responsive web UI is sufficient).
- No cloud hosting, CI/CD, or UI strategy management in R1 (all roadmap, §17).

### Honest expectations (record in README)
This is a retail bot with modest, well-documented strategy classes. The goal of v1 is a **correct, safe, auditable system** and a fair A/B comparison on paper money — not guaranteed profit. Most retail strategies underperform buy-and-hold after costs; the system must measure honestly rather than assume success.

---

## 2. External Constraints (Trading 212 API)

These constraints are facts of the platform and must shape the implementation. Source: https://docs.trading212.com/api (API is **beta**; re-verify at implementation time — the OpenAPI bundle is downloadable at `https://docs.trading212.com/_bundle/api.json`).

| # | Constraint | Implication |
|---|------------|-------------|
| C1 | Two environments: demo `https://demo.trading212.com/api/v0`, live `https://live.trading212.com/api/v0` | Base URL must be config-driven. Default: demo. |
| C2 | API enabled only for Invest and Stocks ISA accounts | Long-only. No shorting logic anywhere. |
| C3 | Orders execute only in the **primary account currency**; multi-currency not supported | Universe must be tradable in the account currency; record account currency at startup and validate. |
| C4 | Auth: HTTP Basic with `API_KEY:API_SECRET` base64-encoded | Secrets via env vars only. Support optional IP restriction (user configures in T212 app). |
| C5 | **Rate limits are per-account**, per-endpoint, regardless of API key or IP. Responses include `x-ratelimit-limit/-period/-remaining/-reset/-used` headers. Limits allow bursts (e.g. 50/min example). | Broker client MUST read these headers and self-throttle. A single global request budget manager must serialize all T212 calls. |
| C6 | Sell orders are expressed as **negative quantity** | Encapsulate in the broker client; never leak this convention into strategy code. |
| C7 | Order endpoints exist for `market`, `limit`, `stop`, `stop_limit` (beta; live support for non-market types is uncertain and may differ from demo) | **Capability detection at runtime** (see §8.3). Prefer broker-side stops; fall back to bot-enforced stops. |
| C8 | The API provides **no historical price candles** | Market data must come from an external source (§7). T212 is execution + account state only. |
| C9 | Positions endpoint returns current quantity, average price, and current P&L | Can serve as a secondary "current price" source for open positions in the monitor loop. |
| C10 | List endpoints use cursor pagination via `nextPagePath` (limit max 50) | History sync jobs must follow `nextPagePath` until null. |
| C11 | Duplicate request risk: re-sending an order request may create duplicate orders | Idempotency layer required (§8.4). |
| C12 | Max 50 pending orders per ticker per account | Not a practical constraint for this design, but enforce a sanity cap anyway. |

---

## 3. Architecture

```
┌────────────────── docker-compose (owner's machine) ──────────────────┐
│                                                                       │
│  ┌──────────────┐  REST: reads + control          ┌───────────┐      │
│  │   React UI   │◀───────────────────────────┐    │ Postgres  │      │
│  │ TanStack     │  WebSocket/SSE: change     │    │ app tables│      │
│  │ Query + WS   │◀────── events ───────┐     │    │ + DBOS    │      │
│  └──────────────┘                      │     │    │ system    │      │
│                     ┌──────────────────┴─────┴─┐  │ schema    │      │
│                     │      FastAPI Backend      │─▶└───────────┘      │
│                     │                           │                     │
│                     │  DBOS durable workflows:  │                     │
│                     │   ├─ Signal (hourly)      │                     │
│                     │   ├─ Monitor (~45 s)      │                     │
│                     │   ├─ Order (per order)    │                     │
│                     │   └─ Sync / daily roll    │                     │
│                     │            │              │                     │
│                     │     ┌──────▼───────┐      │                     │
│                     │     │ Risk Manager │      │                     │
│                     │     └──────┬───────┘      │                     │
│                     │   ┌────────▼────────────┐ │                     │
│                     │   │ Broker Client (T212)│ │                     │
│                     │   │ rate-limited,       │ │                     │
│                     │   │ idempotent          │ │                     │
│                     │   └─────────────────────┘ │                     │
│                     └─────────────┬──────┬──────┘                     │
└───────────────────────────────────┼──────┼───────────────────────────┘
                                    ▼      ▼
                         Trading 212 API   Market data (yfinance)
                         (demo or live)    (hourly candles, quotes)
```

**Core principle: every order flows through one path.**
`Strategy signal → Risk Manager (approve/deny/size) → Order workflow (DBOS) → Broker Client → DB record`. No component may place an order directly. The Risk Manager is the single gatekeeper.

**Second principle: the database is the audit log, T212 is the source of truth for positions.** On any disagreement, reconcile from T212 (§9.4).

**Third principle: the backend owns realtime.** The UI reads via REST and subscribes to a WebSocket/SSE channel the backend publishes on whenever it writes (fills, decisions, snapshots, events, state changes). No polling loops in the UI; no database-level sync machinery (Electric/Supabase Realtime). Because realtime is owned by the application, the same mechanism works unchanged on a laptop, a VPS, or a hosted platform — the R2 hosting move (§17) is a configuration change, not a data-layer rewrite.

---

## 4. Technology Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Backend | Python 3.12, FastAPI, Uvicorn | Async-capable; REST for reads + control, WebSocket/SSE for realtime push |
| Workflows / scheduling | **DBOS Transact** (Python library, self-hosted; **Conductor not used**) | Durable, Postgres-backed workflows replace cron schedulers: scheduled workflows for signal/monitor/sync cadences; per-order workflows with checkpointed steps that survive crashes/restarts |
| Database | **PostgreSQL 16** via SQLAlchemy 2.x ORM + Alembic migrations | Single Postgres instance hosts app tables and the DBOS system schema |
| Real-time push | **FastAPI WebSocket** (SSE acceptable fallback) | Backend broadcasts change events after each write; frontend invalidates/patches TanStack Query caches on message. One channel, topic-tagged messages |
| Market data | `yfinance` | Free hourly candles; isolate behind a `MarketData` interface so the provider is swappable (§7) |
| HTTP client | `httpx` | For T212 client; supports timeouts/retries |
| Config | `pydantic-settings` reading `.env` | Typed, validated config; `.env.example` in repo |
| Frontend | React 18 + Vite, TypeScript | TanStack Query for data fetching with WS-driven invalidation; charting: `recharts` |
| Container | Docker + docker-compose | **Two services:** `app` (FastAPI + DBOS + built UI), `postgres` |
| Testing | pytest, pytest-asyncio; Vitest for UI | See §13 |
| Lint/format | ruff + mypy (backend); eslint + prettier (frontend) | Run locally via a `make check` target + pre-commit hooks; GitHub Actions CI is roadmap (§17) |

---

## 5. Configuration

All via environment variables (`.env` locally). **No secrets committed.** Repo contains `.env.example` with every variable documented.

Strategy allocations and parameters are **seeded** from env on first boot but the **database is authoritative** thereafter (`strategies.allocation`, `strategies.params` — §6, §11). This keeps R1 simple while making the R2 strategy-management UI (§17) a pure UI layer over existing data.

```ini
# --- Broker ---
T212_API_KEY=                 # required at runtime, no default
T212_API_SECRET=              # required at runtime, no default
T212_ENV=demo                 # demo | live  (default demo; switching to live requires LIVE_TRADING_ACK=I_UNDERSTAND)
LIVE_TRADING_ACK=             # safety interlock for live mode

# --- Universe ---
UNIVERSE=SPY,QQQ,IWM,DIA,XLK,XLF,XLE,GLD   # comma-separated; mapped to T212 tickers via instrument metadata (§8.5)

# --- Scheduling ---
SIGNAL_INTERVAL=1h            # strategy evaluation cadence
MONITOR_INTERVAL_SECONDS=45   # risk/reconciliation loop (30–60 recommended)
MARKET_TZ=America/New_York

# --- Capital allocation (SEED VALUES — DB is authoritative after first boot) ---
TREND_ALLOCATION=0.5
MEANREV_ALLOCATION=0.5

# --- Risk (see §9) ---
RISK_PER_TRADE=0.02
MAX_POSITIONS_PER_STRATEGY=4
DAILY_LOSS_LIMIT=0.03
MAX_DRAWDOWN_HALT=0.15
STOP_LOSS_ATR_MULT=2.0
CASH_BUFFER=0.05

# --- Strategy parameters (SEED VALUES — DB `strategies.params` is authoritative after first boot; see §6) ---
TREND_FAST_MA=20
TREND_SLOW_MA=50
TREND_REGIME_MA=200
MEANREV_RSI_PERIOD=14
MEANREV_RSI_ENTRY=30
MEANREV_RSI_EXIT=55
MEANREV_BB_PERIOD=20
MEANREV_BB_STD=2.0
MEANREV_TIME_STOP_BARS=48     # exit after N hourly bars regardless

# --- App ---
DATABASE_URL=postgresql://bot:${POSTGRES_PASSWORD}@postgres:5432/trading_bot
POSTGRES_PASSWORD=            # required; used by compose + DATABASE_URL
LOG_LEVEL=INFO
UI_PORT=8000
```

Validation rules: enabled strategies' allocations must sum to ≤ 1.0; `T212_ENV=live` refuses to start unless `LIVE_TRADING_ACK=I_UNDERSTAND`; missing API keys → app starts in **"unconfigured" mode** (UI loads, shows setup instructions, scheduler disabled).

R1 runs with **no auth**: the app binds to localhost (or the compose network) and serves a single local user. When the backend is hosted (§17), an auth layer (Supabase Auth / JWT) is added in front of both REST and the WebSocket — this is a deliberate R2 task, not an R1 omission to patch silently.

---

## 6. Strategies

Strategies are a **pluggable registry**, not a fixed pair. Each strategy implements a common interface so the engine treats them identically:

```python
class Strategy(Protocol):
    name: str
    def evaluate(self, candles: dict[str, DataFrame], portfolio: StrategyPortfolio) -> list[Signal]
```

A `Signal` is `{ticker, action: OPEN|CLOSE, reason: str, stop_price: float | None}`. Strategies **never size positions and never place orders** — they emit intents; the Risk Manager sizes and approves.

### 6.0 Strategy registry & lifecycle
- Strategy classes register themselves in a code-level registry (decorator or explicit list). Each declares its `name`, typed parameter schema, and parameter defaults.
- On startup, the registry reconciles with the `strategies` table: missing rows are inserted with seed allocation/params from config; existing rows win (DB authoritative). A registered class with `enabled=false` in the DB is loaded but never evaluated.
- **Adding strategy #3 in R1:** write the class, register it, restart. No schema change, no engine change. Everything downstream — ledgers, risk limits, UI comparison — works on "all enabled strategies", never on a hardcoded pair.
- Engine, Risk Manager, reconciliation, and UI must contain **no assumption that N = 2**.

Indicator computation: use a maintained TA library (`pandas-ta` or equivalent) or implement SMA/RSI/Bollinger/ATR directly with unit tests against known values. All parameters from the DB `params` (seeded from §5).

### 6.1 Strategy A — Trend-Following ("trend")
Evaluated on **hourly candles**, per ticker:
- **Regime filter:** only eligible if price > 200-period SMA (hourly).
- **Entry:** fast SMA (20) crosses above slow SMA (50) on a *completed* bar, and no open position in this ticker for this strategy.
- **Exit:** fast SMA crosses below slow SMA, **or** stop-loss hit (2.0 × ATR(14) below entry), **or** circuit breaker (§9).
- One position per ticker per strategy; no pyramiding in v1.

### 6.2 Strategy B — Mean Reversion ("meanrev")
Evaluated on hourly candles, per ticker:
- **Regime filter:** only eligible if price > 200-period SMA (only buy dips in uptrends).
- **Entry:** RSI(14) < 30 **and** close < lower Bollinger Band (20, 2σ) on a completed bar.
- **Exit:** RSI > 55 **or** close ≥ middle Bollinger Band, **or** time stop (48 hourly bars), **or** stop-loss (2.0 × ATR(14)), **or** circuit breaker.

### 6.3 Signal hygiene rules (all strategies)
- Signals computed **only on completed candles**; the in-progress bar is discarded.
- If data for a ticker is stale (last candle older than 2 intervals), skip that ticker and log a `DATA_STALE` event.
- Conflicting same-tick signals for one ticker across strategies are allowed (separate ledgers, §6.4); conflicting signals within one strategy resolve to CLOSE wins.

### 6.4 Virtual ledger / capital split
The T212 account is one pot of money; the bot maintains **one virtual sub-portfolio per enabled strategy** in the DB:
- Each strategy's buying power = its allocation × total bot equity, minus its open exposure. Allocations live in `strategies.allocation` (seeded from config, §5).
- Every order is tagged with `strategy` at creation and the tag is immutable.
- If multiple strategies hold the same ticker, the broker shows one combined position; the DB ledger attributes lots per strategy (FIFO within each strategy's own lots). Reconciliation (§9.4) must handle this: combined broker quantity must equal the sum of all ledgers' quantities for that ticker.
- Equity snapshots per strategy are recorded every monitor cycle for the UI equity curves.

---

## 7. Market Data

- Provider v1: **yfinance**, hourly interval, fetching the configured universe in one batched call per signal cycle plus lightweight quote checks in the monitor loop.
- Wrap in a `MarketDataProvider` interface (`get_candles(tickers, interval, lookback)`, `get_quote(tickers)`) so a paid/real-time provider can replace it without touching strategies.
- **Caching:** persist fetched candles to a `candles` table; on each cycle, fetch only the delta. This gives free backtest data over time and resilience to provider hiccups.
- **Failure policy:** if the provider fails for a cycle → skip signal evaluation (do nothing), log `DATA_OUTAGE`, alert in UI banner. The monitor loop falls back to T212 positions P&L (C9) for stop checks during outages.
- Note in README: yfinance data may be slightly delayed and is unofficial; acceptable for hourly paper trading, should be upgraded before serious live use.

---

## 8. Broker Integration (Trading 212 Client)

A single module `broker/t212.py`, the only code that talks to T212.

### 8.1 Endpoints used
- `GET /equity/account/summary` and `/equity/account/cash` — equity, cash, currency
- `GET /equity/metadata/instruments` — ticker mapping & tradability (§8.5)
- `GET /equity/positions` — open positions with current P&L
- `POST /equity/orders/market`, `/orders/stop` (capability-gated), `/orders/limit` (future)
- `GET /equity/orders`, `GET /equity/orders/{id}`, `DELETE /equity/orders/{id}`
- `GET /equity/history/orders`, `/history/transactions` — paginated sync into DB

### 8.2 Rate-limit governor (critical)
- All requests pass through one async queue (global semaphore = 1 in-flight + spacing).
- After every response, store `x-ratelimit-remaining` / `x-ratelimit-reset` per endpoint; if remaining < 20% of limit, delay subsequent calls to that endpoint until reset.
- On HTTP 429: exponential backoff with jitter, max 5 retries, then mark cycle failed (never drop an intended SELL silently — failed protective orders escalate to `CRITICAL` and re-queue at top priority).
- Priority ordering when budget is tight: protective exits > entries > reconciliation > history sync.

### 8.3 Stop-order capability detection
On startup (and once per day):
1. Attempt to discover stop-order support in the current environment (place a far-away stop on a 1-share equivalent in demo, or rely on a config override `FORCE_BOT_SIDE_STOPS=true`).
2. If supported → on every fill, immediately place a broker-side stop (`/orders/stop`) at the strategy's stop price; the monitor loop verifies the stop exists each cycle and re-creates it if missing.
3. If unsupported/rejected → bot-side stops: monitor loop compares current price to stop; breach → market sell. Log mode prominently; show in UI header ("Stops: broker-side ✅ / bot-side ⚠️").
4. Document in README: bot-side mode means a dead (or sleeping — R1 runs on a personal machine) server = unprotected positions (gap risk exists in both modes when markets are closed).

### 8.4 Idempotency & order lifecycle (DBOS durable order workflow)
Every order is executed as a **DBOS workflow** with checkpointed steps, giving crash-safe, resumable execution:
1. **Step 1 — record intent:** write an `orders` row (`PENDING_SUBMIT`) with a locally generated `client_ref`. (Checkpointed: a crash after this step resumes here, never re-creating the row.)
2. **Step 2 — submit:** POST to T212. On success, store the T212 order id → `SUBMITTED`.
3. **Step 2 recovery rule:** T212's POST is **not idempotent** (C11), so if the workflow recovers with step 2 in an ambiguous state (timeout/5xx/crash mid-call), it must **not blindly re-run the POST**. Instead it runs a *verify* step: fetch open orders + recent fills and look for an order matching `client_ref` context (ticker, qty, time window). Found → adopt it; not found → safe to resubmit.
4. **Step 3 — confirm:** poll/reconcile to terminal status using T212 statuses (`NEW`, `FILLED`, `PARTIALLY_FILLED`, `CANCELLED`, `REJECTED`, …) and update positions/ledgers.
Partial fills: position quantity follows actual fills; remainder handling = cancel after 2 monitor cycles and re-evaluate. Protective (exit/stop) order workflows are enqueued at higher priority than entries on a DBOS queue so rate-limit pressure never starves an exit.

### 8.5 Instrument mapping
T212 uses suffixed tickers (e.g. `SPY_US_EQ`). On startup, fetch `/equity/metadata/instruments`, build a map from config universe symbols → T212 tickers, validate each is tradable in the account currency (C3); untradable symbols are dropped with a logged warning and UI banner.

---

## 9. Risk Management

The Risk Manager approves/denies/sizes every signal. All thresholds from config (§5 defaults).

### 9.1 Position sizing
`risk_amount = strategy_equity × RISK_PER_TRADE` (default 2%).
`quantity = risk_amount / (entry_price − stop_price)`, capped so position value ≤ 25% of strategy equity, and respecting `CASH_BUFFER` (5% of account always uncommitted). Fractional quantities allowed where the instrument supports them; otherwise floor to whole shares; if resulting quantity ≤ 0, deny with reason `TOO_SMALL`.

### 9.2 Limits (per strategy unless stated)
- Max open positions: `MAX_POSITIONS_PER_STRATEGY` (default 4).
- Max one open position per ticker per strategy.
- **Daily loss limit:** if a strategy's realized + unrealized P&L today ≤ −3% of its equity at day start → that strategy stops opening positions until the next trading day (existing stops remain active).
- **Account drawdown halt:** if total bot equity falls ≥ 15% below its high-water mark → `HALTED`: close nothing automatically, open nothing, require human resume via UI. (Rationale: auto-liquidating at max drawdown can crystallize a temporary spike; human decides.)
- No new entries in the final 15 minutes of the session; no entries in the first 15 minutes (open auction noise).

### 9.3 Bot states
`RUNNING` → normal. `PAUSED` (human-initiated via UI): no new entries; exits and stops still enforced. `HALTED` (circuit breaker): same as paused but requires explicit human acknowledgment to resume. `UNCONFIGURED`: no API keys; scheduler off. State persists across restarts.

### 9.4 Reconciliation (startup + every monitor cycle)
- Fetch T212 positions & open orders; compare to DB ledgers.
- Discrepancies (e.g., manual trades made in the T212 app, missed fills): adopt broker as truth for quantities, attribute unexplained quantity to a special `manual` ledger (excluded from strategy stats), log `RECONCILE_DIFF`, and show a UI warning. Never "correct" the broker by trading to match the DB.
- On startup after downtime: reconcile first, re-establish missing stops, then resume loops. (R1 note: "downtime" includes every laptop sleep/wake — this path is routine, not exceptional, and must be robust.)

### 9.5 Market-hours guard
All trading actions gated on NYSE calendar (`pandas-market-calendars`), including half-days and holidays, in `America/New_York`. Outside hours, the monitor loop runs in degraded "watch" mode (no orders possible) at a slow cadence.

---

## 10. Workflows & Scheduling (DBOS)

All recurring work runs as **DBOS scheduled workflows** (cron-style schedules persisted in Postgres; exactly-once semantics per scheduled instant, automatic recovery of interrupted workflows on restart). Conductor is **not** used — the self-hosted library alone is sufficient; observability comes from the DBOS system tables, surfaced in the UI events page.

| Workflow | Schedule | Responsibilities |
|----------|----------|------------------|
| **Signal workflow** | Hourly at HH:01 ET during market hours | Fetch/refresh candles → run all enabled strategies → Risk Manager → spawn order workflows (§8.4) for approved signals → record all decisions (including denials) |
| **Monitor workflow** | Every `MONITOR_INTERVAL_SECONDS` (default 45 s) during market hours | Update quotes/position P&L → enforce bot-side stops (if in that mode) → verify broker-side stops exist → check daily-loss & drawdown breakers → reconcile fills → write equity snapshots |
| **History sync workflow** | Every 15 min + on startup | Paginate `/history/orders` and `/history/transactions` into Postgres (C10) |
| **Daily roll workflow** | At market open | Reset daily-loss baselines, refresh instrument metadata, re-detect stop capability, prune logs |

Rules:
- Market-hours gating happens inside each workflow (first step checks the NYSE calendar and exits early when closed) so schedules stay simple.
- Workflow runs must be **non-overlapping** per type: a new monitor run skips if the previous one is still in flight (guard via DBOS queue with concurrency 1).
- On process startup: DBOS recovers any interrupted workflows (e.g., an order workflow that died mid-submit) **before** the reconciliation step (§9.4) declares the system healthy; then scheduled workflows resume.
- Every signal-workflow decision is persisted — including "no action" and denials — so the UI can answer *why* the bot did or didn't trade. This is the audit trail.

---

## 11. Data Model (PostgreSQL, via Alembic migrations)

DBOS keeps its own system schema in the same database. Tables marked **[ui]** are surfaced to the frontend via REST reads + WebSocket change events (§12); all others are backend-internal.

- **instruments** (symbol, t212_ticker, currency, tradable, min_qty) **[ui]**
- **candles** (symbol, interval, ts, o, h, l, c, v) — unique(symbol, interval, ts)
- **strategies** (id, name, allocation, params JSON, enabled) — seeded from the code registry + config on first boot (§6.0); rows for `trend`, `meanrev`, plus the special `manual` ledger. **DB is authoritative** for allocation/params after seeding **[ui]**
- **orders** (id, client_ref, t212_order_id, strategy_id, symbol, side, qty, type, status, requested_at, filled_at, fill_price, reason, raw_response JSON) **[ui]**
- **positions** (id, strategy_id, symbol, qty, avg_entry, stop_price, stop_mode broker|bot, opened_at, closed_at, realized_pnl, status OPEN|CLOSED) **[ui]**
- **decisions** (ts, loop, strategy_id, symbol, signal, action_taken, deny_reason, context JSON) **[ui]**
- **equity_snapshots** (ts, strategy_id, equity, cash, open_exposure, drawdown) **[ui]**
- **events** (ts, level, code, message, context JSON) — system log surfaced to UI **[ui]**
- **bot_state** (singleton: state, halted_reason, hwm_equity, day_start_equity per strategy) **[ui]**

P&L definitions: realized P&L per closed lot (FIFO within strategy); win = realized P&L > 0 after estimated costs. Store and display per-trade and aggregate: win rate, profit factor, average win/loss, max drawdown, exposure %.

---

## 12. Backend API: REST + WebSocket

The UI reads over plain REST and stays current via a WebSocket event channel — no UI polling loops, no database-level sync service.

**REST reads** (paginated/filterable where it matters):
- `GET /api/positions`, `GET /api/orders`, `GET /api/decisions`, `GET /api/events`, `GET /api/equity-snapshots`, `GET /api/strategies`, `GET /api/instruments` — the **[ui]** tables (§11)
- `GET /api/status` — bot state, environment (DEMO/LIVE badge), stop mode, data freshness, rate-limit headroom
- `GET /api/performance` — per-strategy derived stats block (win rate, profit factor, max DD, total P&L, trade count)
- `GET /api/config` — sanitized config (never returns secrets)

**Realtime:**
- `GET /api/ws` — WebSocket (SSE fallback acceptable). The backend publishes a topic-tagged message after every meaningful write: `{topic: "orders" | "positions" | "decisions" | "events" | "equity" | "bot_state" | "strategies", op, payload?}`. Small rows ship inline as payloads; otherwise the message is an invalidation hint and the frontend refetches the affected query. Frontend wires messages to TanStack Query cache invalidation — every view updates live with no polling.

**Control writes:**
- `POST /api/control/pause`, `POST /api/control/resume` (resume from HALTED requires `{"acknowledge": true}`)

**Auth (R1): none.** The app serves localhost for a single local user; CORS locked to the serving origin. Adding auth (Supabase Auth / JWT on REST + WS handshake) is an explicit precondition of hosted deployment (§17) — the API layer should be written so an auth dependency can be inserted in one place.

## 12.1 Frontend (React) — pages
1. **Dashboard:** state banner (RUNNING/PAUSED/HALTED + DEMO/LIVE badge), total & per-strategy equity curves overlaid, open positions table, today's P&L, pause/resume button (resume-from-halt has confirm dialog).
2. **Trades:** filterable history table (strategy, symbol, win/loss, date range); row expands to show entry/exit orders, stop history, and the decision context that opened it.
3. **Strategy comparison:** side-by-side stat cards + normalized equity curves (all starting at 100) for **every enabled strategy** — layout must handle N strategies, not a fixed two; per-symbol breakdown.
4. **Activity/Events:** decisions log and system events with level filters; data-freshness and rate-limit indicators.
Data layer: TanStack Query over the REST endpoints, with the WebSocket channel driving cache invalidation — positions, trades, decisions, events, and equity curves update in real time as the backend writes, with no polling. Charting with recharts; responsive layout.

---

## 13. Testing & Verification

- **Unit:** indicators vs known-good values; sizing math; ledger/FIFO P&L; rate-limit governor (simulated headers); idempotent submit logic (simulated timeouts); strategy registry reconciliation (registry ↔ DB seeding rules, §6.0).
- **Integration:** fake T212 server (respx/httpx mock) covering fills, partial fills, 429s, rejected stops, pagination; full signal→risk→order→reconcile path.
- **Durability:** kill the process mid-order-workflow (after step 1, during step 2) and assert DBOS recovery resumes correctly without duplicate orders (verify-before-resubmit, §8.4); assert scheduled workflows resume after restart.
- **Realtime path:** integration test that a backend write to a [ui] table produces the corresponding WebSocket message, and that a connected client's query invalidation fires.
- **Backtest harness (required deliverable):** replay cached candles through the *same* strategy + risk code (not a reimplementation) with modeled costs (spread + slippage estimate per ETF) to validate behavior and set expectations. Out-of-sample split documented. Build it as soon as the strategy engine exists (E4) — it is the cheapest source of evidence on whether the seeded strategies are worth running, and should inform parameters before the paper run starts.
- **Paper-trading acceptance:** ≥ 4 weeks on demo with zero reconciliation errors, zero unprotected positions (while the host machine was awake), and UI stats matching T212's own history export before any live discussion.
- Quality gates in R1: `make check` (ruff, mypy, eslint, pytest, vitest) run locally + pre-commit hooks. GitHub Actions CI arrives with R2 (§17).

---

## 14. Proposed Epic Breakdown (for sub-task decomposition)

1. **E1 Foundations:** repo scaffold, config system, docker-compose (`app` + `postgres`), DB schema + Alembic migrations, DBOS initialization, logging/events, `make check` + pre-commit.
2. **E2 Broker client:** auth, rate-limit governor, instruments, positions, orders (market), history sync, DBOS order workflow with verify-before-resubmit (§8.4), fake-T212 test server.
3. **E3 Market data:** provider interface, yfinance impl, candle cache, staleness handling.
4. **E4 Strategy engine:** Strategy interface + registry (§6.0), indicators, trend + meanrev implementations, signal hygiene, unit tests. **Backtest harness starts here.**
5. **E5 Risk & ledgers:** sizing, limits, breakers, N virtual ledgers, bot states, reconciliation.
6. **E6 Workflows:** market calendar gating, the four DBOS scheduled workflows, non-overlap guards, startup sequence (DBOS recovery → reconcile → stops → resume).
7. **E7 Stops:** capability detection, broker-side placement/verification, bot-side fallback.
8. **E8 Backend API:** REST read endpoints, control endpoints, WebSocket event channel.
9. **E9 Frontend:** TanStack Query + WS invalidation data layer, then the four pages.
10. **E10 Backtest harness completion + paper-trading acceptance run + README/runbook.**

Dependency order: E1 → (E2, E3 parallel) → E4 → E5 → E6/E7 → E8 → E9; E10 last but the harness starts at E4.

---

## 15. Running & Repo (R1: local)

- **Repo layout:** `backend/` (FastAPI app: `broker/`, `strategies/`, `risk/`, `workflows/`, `api/`, `models/`), `frontend/`, `docker-compose.yml`, `.env.example`, `README.md`, `docs/` (this spec), `Makefile`.
- `.gitignore` covers `.env` and build artifacts. **A pre-commit secret-scan (e.g. gitleaks) is required** since the repo is public-bound.
- **docker-compose services:** `postgres` (PG 16, named volume, not port-exposed beyond localhost), `app` (FastAPI + DBOS + built React UI on `UI_PORT`). Healthcheck endpoint `/api/status`. Host machine runs the bot during market hours; app logic pinned to `America/New_York` regardless of host timezone.
- **Single app instance:** DBOS supports multi-instance recovery, but rate limits are per-account (C5) and the broker-client budget manager is in-process — running two app replicas would double-spend the budget. Enforce one instance and document it.
- Backups: periodic `pg_dump` of the Postgres volume (covers app tables and DBOS workflow state together).
- Runbook in README: key generation in the T212 app (key + secret shown once; recommend IP restriction), starting/stopping the bot (`docker compose up -d` / `down`), what happens when the machine sleeps (bot stops; on wake → DBOS recovery → reconcile → resume), demo → live promotion checklist (acceptance criteria from §13, the `LIVE_TRADING_ACK` interlock, **and hosted deployment per §17 — live trading from a laptop is explicitly not supported**), Postgres backup/restore, what HALTED means and how to resume.

---

## 16. Open Risks (carry into README)

1. **T212 API is beta** — endpoints/limits may change; the OpenAPI bundle should be re-checked at build time. Stop/limit order support in *live* is unverified until tested with real keys.
2. **yfinance** is unofficial and may be delayed/throttled; acceptable for hourly paper trading, upgrade before live.
3. **R1 runs on a personal computer** — machine asleep or off means no signal evaluation, no bot-side stop enforcement, and no monitoring. Acceptable for demo money; a hard blocker for live (§17). Broker-side stops (when supported) mitigate but don't eliminate this; overnight gap risk exists in all modes.
4. **Hourly strategies on liquid ETFs trade infrequently** — weeks of few/no trades is normal, not a bug; the decisions log exists to prove the bot is alive and choosing not to trade.
5. **No performance guarantee** — the A/B framework measures honestly; any or all strategies may underperform buy-and-hold after costs.

---

## 17. Roadmap (R2+, explicitly out of scope for R1)

Sequenced, each step independent of the others where possible. The R1 architecture choices above (backend-owned realtime, DB-authoritative strategy config, single-point auth insertion) exist to make these steps additive rather than rewrites.

1. **Hosted deployment:** backend container on Railway (or Fly/VPS) — required because the bot needs an always-on process (~$5/mo); database moves to **Supabase** managed Postgres (app tables + DBOS schema). No data-layer changes: realtime stays backend-owned, so swapping `DATABASE_URL` and the host is the bulk of the work.
2. **Auth:** Supabase Auth (or simple JWT) in front of REST + WebSocket handshake. Single owner account.
3. **CI/CD:** GitHub Actions running `make check` on every PR; auto-deploy `main` to the hosting platform.
4. **UI-level strategy management:** edit allocations and params, enable/disable strategies from the UI (writes to the `strategies` table, which has been authoritative since R1); guarded by validation (allocations ≤ 1.0, param schema per strategy).
5. **Live trading:** only after §13 paper acceptance criteria pass **and** steps 1–3 are done; gated by the `LIVE_TRADING_ACK` interlock; market-data provider upgrade recommended (§7).
