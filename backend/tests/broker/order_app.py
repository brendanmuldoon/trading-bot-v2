"""Subprocess harness for T11 durability tests.

Usage: python order_app.py <db_url> <fake_base_url> <strategy_id> [--start]

Wires the broker stack against the parent-served fake T212, launches
DBOS (which recovers interrupted workflows), and with --start enqueues
one BUY order workflow. The parent kills this process at scripted
moments and asserts zero duplicate orders after recovery.
"""

import sys
import time

from dbos import DBOS, DBOSConfig

from backend.broker.api import BrokerAPI
from backend.broker.governor import Governor
from backend.broker.t212 import T212Client
from backend.db import get_engine, get_sessionmaker
from backend.models import OrderSide
from backend.workflows import runtime
from backend.workflows.order import submit_order


def main() -> None:
    db_url, base_url, strategy_id = sys.argv[1], sys.argv[2], int(sys.argv[3])
    start = "--start" in sys.argv

    client = T212Client("k", "s", base_url=base_url, timeout=5.0)
    governor = Governor(client, spacing=0.0)
    runtime.configure(BrokerAPI(governor), get_sessionmaker(get_engine(db_url)))

    config: DBOSConfig = {
        "name": "order-app",
        "system_database_url": db_url,
        "application_version": "t11-test",
    }
    DBOS(config=config)
    DBOS.launch()

    if start:
        submit_order(
            strategy_id=strategy_id,
            ticker="SPY_US_EQ",
            side=OrderSide.BUY,
            quantity=2.0,
            reason="durability test entry",
            protective=False,
        )

    time.sleep(30)  # parent kills us or we exit after the workflow is long done
    DBOS.destroy()


if __name__ == "__main__":
    main()
