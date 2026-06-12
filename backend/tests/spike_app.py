"""Standalone DBOS app for the T05 durability spike.

Usage: python backend/tests/spike_app.py <database_url> [--start]

The workflow writes a 'started' probe row (step 1), durably sleeps,
then writes a 'finished' row (step 2). The parent test kills this
process mid-sleep and relaunches without --start; DBOS recovery must
resume the workflow without re-running step 1.
"""

import sys
import time

from dbos import DBOS, DBOSConfig
from sqlalchemy import create_engine, text

DATABASE_URL = sys.argv[1]
START = "--start" in sys.argv

engine = create_engine(DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1))


def record(phase: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO spike_probe (phase) VALUES (:p)"), {"p": phase})


@DBOS.step()
def step_started() -> None:
    record("started")


@DBOS.step()
def step_finished() -> None:
    record("finished")


@DBOS.workflow()
def spike_workflow() -> None:
    step_started()
    DBOS.sleep(5)
    step_finished()


def main() -> None:
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS spike_probe (phase text)"))

    config: DBOSConfig = {"name": "spike", "system_database_url": DATABASE_URL}
    DBOS(config=config)
    DBOS.launch()  # recovers any interrupted workflow runs

    if START:
        DBOS.start_workflow(spike_workflow)

    # Stay alive so the workflow can run (parent kills or waits us out).
    time.sleep(30)
    DBOS.destroy()


if __name__ == "__main__":
    main()
