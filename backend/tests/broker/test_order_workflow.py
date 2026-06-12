"""T11: durable order workflow — happy path, ambiguous submits, partial
fills, and subprocess kill/restart durability with zero duplicates."""

import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import uvicorn
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from backend.broker.api import BrokerAPI
from backend.broker.governor import Governor
from backend.broker.t212 import T212Client
from backend.db import get_sessionmaker
from backend.models import Order, OrderSide, OrderStatus, Strategy
from backend.tests.broker.fake_t212 import API, FakeT212
from backend.tests.conftest import REPO_ROOT
from backend.workflows import runtime
from backend.workflows.order import submit_order

MARKET_PATH = f"{API}/equity/orders/market"
ORDERS_PATH = f"{API}/equity/orders"


# ---------- fixtures ----------------------------------------------------------


@pytest.fixture()
def fake() -> FakeT212:
    f = FakeT212(api_key="k", api_secret="s")
    f.add_instrument("SPY_US_EQ", price=500.0)
    return f


@pytest.fixture()
def session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return get_sessionmaker(db_engine)


@pytest.fixture(autouse=True)
def clean_orders(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        session.query(Order).delete()
        session.commit()


@pytest.fixture()
def strategy_id(session_factory: sessionmaker[Session]) -> int:
    with session_factory() as session:
        strategy = session.scalar(select(Strategy).where(Strategy.name == "trend"))
        if strategy is None:
            strategy = Strategy(name="trend", allocation=0.5, params={})
            session.add(strategy)
            session.commit()
        return strategy.id


@pytest.fixture()
def dbos_runtime(
    fake: FakeT212, session_factory: sessionmaker[Session], migrated_db_url: str
) -> Iterator[None]:
    """In-process DBOS against the migrated test DB + fake broker stack."""
    from dbos import DBOS, DBOSConfig

    client = T212Client("k", "s", env="demo", transport=fake.transport, timeout=2.0)
    governor = Governor(client, spacing=0.0)
    runtime.configure(BrokerAPI(governor), session_factory)

    DBOS.destroy()
    config: DBOSConfig = {"name": "t11-inproc", "system_database_url": migrated_db_url}
    DBOS(config=config)
    DBOS.launch()
    yield
    DBOS.destroy()


def wait_until(predicate: Callable[[], Any], timeout: float = 20.0, interval: float = 0.2) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    return None


def db_order(session_factory: sessionmaker[Session]) -> Order | None:
    with session_factory() as session:
        order = session.scalars(select(Order)).first()
        if order is not None:
            session.expunge(order)
        return order


# ---------- in-process workflow tests ----------------------------------------


def test_happy_path_checkpointed_to_filled(
    fake: FakeT212, session_factory: sessionmaker[Session], strategy_id: int, dbos_runtime: None
) -> None:
    handle = submit_order(
        strategy_id=strategy_id,
        ticker="SPY_US_EQ",
        side=OrderSide.BUY,
        quantity=2.0,
        reason="trend cross",
        protective=False,
    )
    result = handle.get_result()
    assert result["status"] == "FILLED"
    assert result["adopted"] is False

    order = db_order(session_factory)
    assert order is not None
    assert order.status is OrderStatus.FILLED
    assert order.t212_order_id is not None
    assert order.client_ref.startswith("bot-")
    assert len(fake.orders) == 1


def test_ambiguous_submit_with_order_placed_adopts(
    fake: FakeT212, session_factory: sessionmaker[Session], strategy_id: int, dbos_runtime: None
) -> None:
    """Timeout but the order WAS placed (C11) → verify adopts, no re-POST."""
    fake.queue_timeout(MARKET_PATH, place_order=True)
    handle = submit_order(
        strategy_id=strategy_id,
        ticker="SPY_US_EQ",
        side=OrderSide.BUY,
        quantity=2.0,
        reason="trend cross",
        protective=False,
    )
    result = handle.get_result()
    assert result["adopted"] is True
    assert result["status"] == "FILLED"
    assert len(fake.orders) == 1, "duplicate order created despite verify step"

    order = db_order(session_factory)
    assert order is not None
    assert "adopted" in (order.reason or "")


def test_ambiguous_submit_without_order_resubmits(
    fake: FakeT212, session_factory: sessionmaker[Session], strategy_id: int, dbos_runtime: None
) -> None:
    """Timeout and nothing placed → verify finds nothing → safe resubmit."""
    fake.queue_timeout(MARKET_PATH, place_order=False)
    handle = submit_order(
        strategy_id=strategy_id,
        ticker="SPY_US_EQ",
        side=OrderSide.BUY,
        quantity=2.0,
        reason="trend cross",
        protective=False,
    )
    result = handle.get_result()
    assert result["adopted"] is False
    assert result["status"] == "FILLED"
    assert len(fake.orders) == 1


def test_partial_fill_recorded(
    fake: FakeT212, session_factory: sessionmaker[Session], strategy_id: int, dbos_runtime: None
) -> None:
    fake.fill_mode = "partial"
    fake.partial_ratio = 0.5
    handle = submit_order(
        strategy_id=strategy_id,
        ticker="SPY_US_EQ",
        side=OrderSide.BUY,
        quantity=4.0,
        reason="trend cross",
        protective=False,
    )
    result = handle.get_result()
    assert result["status"] == "PARTIALLY_FILLED"
    assert result["filled_quantity"] == 2.0

    order = db_order(session_factory)
    assert order is not None
    assert order.status is OrderStatus.PARTIALLY_FILLED  # T28 cancels the remainder


# ---------- cross-process durability ------------------------------------------


@pytest.fixture()
def fake_server(fake: FakeT212) -> Iterator[str]:
    """Serve the fake over real HTTP so subprocesses can reach it while
    the parent inspects/controls its state."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(fake.asgi_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    assert wait_until(lambda: server.started, timeout=10), "fake server failed to start"
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def _spawn(db_url: str, base_url: str, strategy_id: int, *, start: bool) -> subprocess.Popen[bytes]:
    script = str(REPO_ROOT / "backend" / "tests" / "broker" / "order_app.py")
    args = [sys.executable, script, db_url, base_url, str(strategy_id)]
    if start:
        args.append("--start")
    return subprocess.Popen(args, env=os.environ | {"PYTHONPATH": str(REPO_ROOT)})


def test_kill_before_post_recovers_with_single_order(
    fake: FakeT212,
    fake_server: str,
    migrated_db_url: str,
    session_factory: sessionmaker[Session],
    strategy_id: int,
) -> None:
    """Kill while step 2 is verifying (intent row exists, nothing POSTed).
    Recovery must complete the order exactly once."""
    fake.queue_hang(ORDERS_PATH)  # step-2 verify call hangs

    proc = _spawn(migrated_db_url, fake_server, strategy_id, start=True)
    try:
        assert wait_until(lambda: db_order(session_factory) is not None), "intent row missing"
        # The hang is consumed the moment the verify GET arrives (the
        # adapter hangs before logging, so check consumption not the log).
        assert wait_until(lambda: not fake.hang_pending(ORDERS_PATH)), "verify call never arrived"
        os.kill(proc.pid, signal.SIGKILL)
    finally:
        proc.wait(timeout=10)

    assert len(fake.orders) == 0, "order placed before kill — test premise broken"

    proc2 = _spawn(migrated_db_url, fake_server, strategy_id, start=False)
    try:
        assert wait_until(
            lambda: (o := db_order(session_factory)) is not None and o.status is OrderStatus.FILLED,
            timeout=30,
        ), "recovery did not complete the order"
    finally:
        proc2.kill()
        proc2.wait(timeout=10)

    assert len(fake.orders) == 1, f"expected exactly 1 broker order, got {len(fake.orders)}"
    order = db_order(session_factory)
    assert order is not None and order.t212_order_id is not None


def test_kill_mid_post_recovers_by_adopting(
    fake: FakeT212,
    fake_server: str,
    migrated_db_url: str,
    session_factory: sessionmaker[Session],
    strategy_id: int,
) -> None:
    """Kill mid-POST with the order actually placed broker-side (C11).
    Recovery must adopt it — zero duplicate orders."""
    fake.queue_hang(MARKET_PATH, place_order_first=True)

    proc = _spawn(migrated_db_url, fake_server, strategy_id, start=True)
    try:
        assert wait_until(lambda: len(fake.orders) == 1, timeout=30), (
            "order never reached the fake broker"
        )
        os.kill(proc.pid, signal.SIGKILL)
    finally:
        proc.wait(timeout=10)

    proc2 = _spawn(migrated_db_url, fake_server, strategy_id, start=False)
    try:
        assert wait_until(
            lambda: (o := db_order(session_factory)) is not None and o.status is OrderStatus.FILLED,
            timeout=30,
        ), "recovery did not complete the order"
    finally:
        proc2.kill()
        proc2.wait(timeout=10)

    assert len(fake.orders) == 1, f"duplicate order on recovery: {len(fake.orders)}"
    order = db_order(session_factory)
    assert order is not None
    assert order.t212_order_id == str(next(iter(fake.orders)))
