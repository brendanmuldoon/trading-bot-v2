"""T05 DBOS spike: scheduled workflows fire; interrupted workflows recover.

This de-risks every later epic (E2 order workflows, E6 scheduling).
"""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import datetime

import pytest
from sqlalchemy import text

from backend.db import get_engine
from backend.tests.conftest import REPO_ROOT, _server_url

SPIKE_DB = "trading_bot_spike"


@pytest.fixture()
def spike_db_url() -> Iterator[str]:
    try:
        admin = get_engine(_server_url()).execution_options(isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {SPIKE_DB} (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {SPIKE_DB}"))
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"Postgres unavailable for spike tests: {exc}")

    yield _server_url().rsplit("/", 1)[0] + f"/{SPIKE_DB}"

    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {SPIKE_DB} (FORCE)"))
    admin.dispose()


def _probe_phases(url: str) -> list[str]:
    engine = get_engine(url)
    try:
        with engine.connect() as conn:
            try:
                rows = conn.execute(text("SELECT phase FROM spike_probe")).scalars().all()
            except Exception:
                return []
            return list(rows)
    finally:
        engine.dispose()


def _wait_for(predicate, timeout: float, interval: float = 0.25):  # type: ignore[no-untyped-def]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return None


def test_scheduled_workflow_fires_and_system_schema_created(spike_db_url: str) -> None:
    from dbos import DBOS, DBOSConfig

    DBOS.destroy()
    config: DBOSConfig = {"name": "spike-sched", "system_database_url": spike_db_url}
    DBOS(config=config)

    fired: list[datetime] = []

    @DBOS.scheduled("* * * * * *")  # six-field crontab: every second
    @DBOS.workflow()
    def tick(scheduled_time: datetime, actual_time: datetime) -> None:
        fired.append(scheduled_time)

    DBOS.launch()
    try:
        assert _wait_for(lambda: len(fired) >= 2, timeout=10.0), "scheduled workflow never fired"
        engine = get_engine(spike_db_url)
        with engine.connect() as conn:
            schemas = (
                conn.execute(text("SELECT schema_name FROM information_schema.schemata"))
                .scalars()
                .all()
            )
        engine.dispose()
        assert "dbos" in schemas, "DBOS system schema missing"
    finally:
        DBOS.destroy()


def test_killed_workflow_recovers_without_duplicate_steps(spike_db_url: str) -> None:
    script = str(REPO_ROOT / "backend" / "tests" / "spike_app.py")
    env = os.environ | {"PYTHONPATH": str(REPO_ROOT)}

    # Run 1: start the workflow, kill -9 mid durable sleep.
    proc = subprocess.Popen([sys.executable, script, spike_db_url, "--start"], env=env)
    try:
        assert _wait_for(lambda: "started" in _probe_phases(spike_db_url), timeout=30.0), (
            "workflow never reached step 1"
        )
        os.kill(proc.pid, signal.SIGKILL)
    finally:
        proc.wait(timeout=10)

    assert "finished" not in _probe_phases(spike_db_url), "kill arrived too late to test recovery"

    # Run 2: relaunch WITHOUT --start; DBOS recovery must finish the workflow.
    proc2 = subprocess.Popen([sys.executable, script, spike_db_url], env=env)
    try:
        assert _wait_for(lambda: "finished" in _probe_phases(spike_db_url), timeout=30.0), (
            "recovered workflow never finished"
        )
    finally:
        proc2.kill()
        proc2.wait(timeout=10)

    phases = _probe_phases(spike_db_url)
    assert phases.count("started") == 1, f"step 1 re-ran on recovery: {phases}"
    assert phases.count("finished") == 1, f"step 2 ran twice: {phases}"
