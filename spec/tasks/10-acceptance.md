# E10 — Backtest Completion, Runbook, Acceptance (T42–T44)

Spec refs: §13, §15, §16. Final epic; the backtest harness skeleton already exists from T20.

---

## Task T42: Backtest harness completion — costs, risk integration, out-of-sample

**Description:** Complete the T20 skeleton into the §13 required deliverable: route replayed signals through the **real** Risk Manager and ledger code (not a reimplementation), model costs (spread + slippage estimate per ETF), document the out-of-sample split, and produce a comparison report (per-strategy stats matching the live stat definitions in §11). Use the results to validate/adjust seed parameters **before** the paper run starts.

**Acceptance criteria:**
- [ ] Replay uses the same Strategy, Risk Manager, sizing, and FIFO ledger code paths as live; deviation only at the execution boundary (simulated fills + cost model)
- [ ] Cost model applied per ETF; in-sample vs out-of-sample split documented in the report output
- [ ] Report covers both seeded strategies over the cached/downloadable history with the §11 stat set; findings recorded in `docs/` and reflected in chosen seed params

**Verification:**
- [ ] `pytest backend/tests/backtest/test_full_harness.py` — costs change results in the expected direction; real-risk-path assertion (e.g. a `TOO_SMALL` denial reproduces in backtest)
- [ ] Manual: full run over ≥ 6 months of hourly data for the universe completes and the report is sane

**Dependencies:** T20, T21–T23

**Files likely touched:** `backend/backtest/harness.py`, `backend/backtest/costs.py`, `backend/backtest/report.py`, `backend/tests/backtest/test_full_harness.py`, `docs/backtest-results.md`

**Estimated scope:** M

---

## Task T43: README, runbook, and honest-expectations documentation

**Description:** The §15 runbook + §16 risk register + §1 honest-expectations note, all in README/docs: T212 key generation (key+secret shown once; recommend IP restriction), start/stop (`docker compose up -d`/`down`), what happens on machine sleep (bot stops; wake → DBOS recovery → reconcile → resume), stop-mode meanings (broker-side vs bot-side, unprotected-while-asleep warning), HALTED semantics and how to resume, Postgres backup/restore (`pg_dump` of the volume), the single-instance constraint, yfinance caveats, demo→live promotion checklist (§13 acceptance criteria + `LIVE_TRADING_ACK` + hosted deployment per §17 — live from a laptop explicitly unsupported), and the five §16 open risks.

**Acceptance criteria:**
- [ ] A new user can go from clone → running paper bot using only the README (walk the steps on a clean checkout to verify)
- [ ] Every §16 risk and the honest-expectations paragraph present; live-promotion checklist explicit
- [ ] `.env.example` audited: every §5 variable documented, no drift from the settings model

**Verification:**
- [ ] Manual clean-checkout walkthrough of the runbook
- [ ] `pytest backend/tests/test_env_example.py` — `.env.example` keys match the settings model exactly

**Dependencies:** Everything (documents the finished system); can draft incrementally from E6 onward

**Files likely touched:** `README.md`, `docs/runbook.md`, `.env.example`, `backend/tests/test_env_example.py`

**Estimated scope:** M

---

## Task T44: Paper-trading acceptance run — procedure and tracking

**Description:** Start and instrument the §13 acceptance run: ≥ 4 weeks on demo with **zero reconciliation errors**, **zero unprotected positions** (while the host machine was awake), and UI stats matching T212's own history export. Deliverable is the procedure + tooling, since the run itself spans a month: an acceptance checklist doc, a weekly verification script/queries (reconcile-diff count, unprotected-position audit from events, stats-vs-T212-export comparison helper), and a log template for weekly checkpoints.

**Acceptance criteria:**
- [ ] Acceptance criteria from §13 written as a checklist with measurable queries/scripts for each
- [ ] Comparison helper ingests a T212 history export and diffs it against DB trade history
- [ ] Run started on demo; week-1 checkpoint completed and logged (full 4-week sign-off happens on calendar time, not in this task)

**Verification:**
- [ ] `python -m backend.acceptance.weekly_check` runs clean against the live paper DB
- [ ] Manual: week-1 log entry exists with zero reconciliation errors (or each diff explained)

**Dependencies:** T30 (full loop live), T36, T41 (observability to audit it), T43

**Files likely touched:** `docs/acceptance-run.md`, `backend/acceptance/weekly_check.py`, `backend/acceptance/t212_export_diff.py`

**Estimated scope:** S

---

## Checkpoint G — R1 complete (see `00-overview.md`)
Backtest-informed parameters, runbook verified on a clean checkout, acceptance run underway with tooling in place.
