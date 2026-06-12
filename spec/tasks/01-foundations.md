# E1 — Foundations (T01–T05)

Repo scaffold, config, compose, schema, DBOS init. Everything else depends on this epic. Spec refs: §4, §5, §11, §15.

---

## Task T01: Repo scaffold, Makefile, lint/format, pre-commit

**Description:** Create the repo layout from §15 (`backend/` with `broker/`, `strategies/`, `risk/`, `workflows/`, `api/`, `models/` packages; `frontend/` Vite + React 18 + TS scaffold; `docker-compose.yml`, `.env.example`, `Makefile`, `docs/`). Wire ruff + mypy for backend, eslint + prettier for frontend, a `make check` target running all of them plus pytest/vitest, and pre-commit hooks including a gitleaks secret scan (repo is public-bound).

**Acceptance criteria:**
- [ ] `make check` runs ruff, mypy, pytest (backend) and eslint, prettier-check, vitest (frontend) and exits 0 on the empty scaffold
- [ ] `pre-commit run --all-files` passes and includes gitleaks; a file containing a fake API key is rejected at commit
- [ ] `.gitignore` covers `.env`, `__pycache__`, `node_modules`, build artifacts; no secrets anywhere in the tree

**Verification:**
- [ ] `make check` exits 0
- [ ] Manual: commit a file with `T212_API_KEY=abc123realsecret` → pre-commit blocks it

**Dependencies:** None

**Files likely touched:** `Makefile`, `.pre-commit-config.yaml`, `pyproject.toml`, `backend/` package skeleton, `frontend/` scaffold, `.gitignore`, `.gitleaks.toml`

**Estimated scope:** M (scaffold-wide but mechanical)

---

## Task T02: Typed config system with validation and unconfigured mode

**Description:** `pydantic-settings` settings module loading every §5 variable from `.env`, with types, defaults, and documented `.env.example`. Implement validation rules: enabled-strategy allocations must sum to ≤ 1.0; `T212_ENV=live` refuses startup unless `LIVE_TRADING_ACK=I_UNDERSTAND`; missing API keys puts the app in `UNCONFIGURED` mode (flag the rest of the app can read) instead of crashing.

**Acceptance criteria:**
- [ ] Every variable in §5 is present in the settings model and in `.env.example` with a comment
- [ ] Live-without-ack and allocations-sum > 1.0 each raise a startup error with a clear message
- [ ] Missing `T212_API_KEY`/`T212_API_SECRET` yields `settings.unconfigured == True`, not an exception

**Verification:**
- [ ] `pytest backend/tests/test_config.py` — cases: defaults, live interlock, allocation sum, unconfigured mode
- [ ] Manual: `T212_ENV=live` without ack → process exits non-zero with explanatory message

**Dependencies:** T01

**Files likely touched:** `backend/config.py`, `.env.example`, `backend/tests/test_config.py`

**Estimated scope:** S

---

## Task T03: docker-compose — postgres + app services

**Description:** Two-service compose per §15: `postgres` (PG 16, named volume, port not exposed beyond localhost) and `app` (Python 3.12 image running FastAPI/Uvicorn, later also DBOS and the built UI, exposed on `UI_PORT`). App healthcheck hits `/api/status`. `POSTGRES_PASSWORD` from env; `DATABASE_URL` composed per §5. Document the single-app-instance constraint (§15) in compose comments.

**Acceptance criteria:**
- [ ] `docker compose up -d` brings both services healthy from a clean checkout with only `.env` populated
- [ ] Postgres data survives `docker compose down` && `up` (named volume)
- [ ] Postgres is not reachable from off-host (no `0.0.0.0` port publish)

**Verification:**
- [ ] `docker compose up -d && docker compose ps` shows both healthy
- [ ] `curl localhost:${UI_PORT}/api/status` returns 200

**Dependencies:** T01, T02 (env vars), T05 (the `/api/status` endpoint — stub acceptable until then)

**Files likely touched:** `docker-compose.yml`, `backend/Dockerfile`, `.env.example`

**Estimated scope:** S

---

## Task T04: Database schema + Alembic migrations

**Description:** SQLAlchemy 2.x models and initial Alembic migration for all §11 tables: `instruments`, `candles` (unique symbol+interval+ts), `strategies`, `orders`, `positions`, `decisions`, `equity_snapshots`, `events`, `bot_state` (singleton). Include enums/check constraints for statuses (`OPEN|CLOSED`, order statuses, bot states) and FKs (`strategy_id`). DBOS will own its separate system schema in the same database — do not model it.

**Acceptance criteria:**
- [ ] `alembic upgrade head` on an empty DB creates every table with the §11 columns and constraints; `alembic downgrade base` is clean
- [ ] `candles` unique constraint and `bot_state` singleton constraint enforced at DB level
- [ ] Model imports pass mypy; a session-scoped test fixture provides a migrated throwaway DB

**Verification:**
- [ ] `pytest backend/tests/test_models.py` — round-trip insert/read per table, constraint violation tests
- [ ] `alembic upgrade head && alembic downgrade base && alembic upgrade head` succeeds

**Dependencies:** T01, T03 (a Postgres to run against)

**Files likely touched:** `backend/models/*.py`, `backend/alembic/versions/0001_initial.py`, `backend/alembic/env.py`, `backend/tests/test_models.py`, `backend/tests/conftest.py`

**Estimated scope:** M

---

## Task T05: FastAPI skeleton + DBOS initialization + events/logging

**Description:** Minimal FastAPI app with `/api/status` (returns bot state, environment badge, configured/unconfigured) and lifespan wiring that initializes DBOS Transact against the same Postgres. Include a tiny **spike test** proving a DBOS scheduled workflow fires and an interrupted workflow recovers on restart — this de-risks every later epic. Add the `events` helper: one function that writes an `events` row and emits a structured log line (UI WebSocket publish is added in T36).

**Acceptance criteria:**
- [ ] App boots in both configured and unconfigured modes; `/api/status` reflects which
- [ ] DBOS system schema appears in Postgres on first boot; a demo scheduled workflow runs on its cadence
- [ ] Spike: kill the process mid-workflow → on restart DBOS resumes it (assert via side-effect table)
- [ ] `log_event(level, code, message, context)` writes to `events` and to stdout logs at `LOG_LEVEL`

**Verification:**
- [ ] `pytest backend/tests/test_dbos_spike.py` (recovery test may use subprocess kill)
- [ ] `curl localhost:8000/api/status` → 200 with state JSON

**Dependencies:** T02, T04

**Files likely touched:** `backend/main.py`, `backend/dbos_init.py`, `backend/events.py`, `backend/api/status.py`, `backend/tests/test_dbos_spike.py`

**Estimated scope:** M

---

## Checkpoint A (see `00-overview.md`)
Compose up, migrations clean, `make check` green, gitleaks firing, unconfigured mode works.
