"""T05: /api/status reflects configured/unconfigured and persisted state."""

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from backend.config import Settings
from backend.db import get_sessionmaker
from backend.main import app
from backend.models import BotState, BotStateRow


def make_client(settings: Settings, session_factory: object | None = None) -> TestClient:
    app.state.settings = settings
    app.state.session_factory = session_factory
    # TestClient used without a context manager: lifespan does not run,
    # so app.state is exactly what we set here.
    return TestClient(app)


def make_settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_status_unconfigured() -> None:
    client = make_client(make_settings(t212_api_key="", t212_api_secret=""))
    body = client.get("/api/status").json()
    assert body["state"] == "UNCONFIGURED"
    assert body["environment"] == "DEMO"
    assert body["configured"] is False


def test_status_configured() -> None:
    client = make_client(make_settings(t212_api_key="k", t212_api_secret="s"))
    body = client.get("/api/status").json()
    assert body["state"] == "RUNNING"
    assert body["configured"] is True


def test_status_reads_persisted_state(db_engine: Engine) -> None:
    session_factory = get_sessionmaker(db_engine)
    with session_factory() as session:
        row = session.get(BotStateRow, 1)
        if row is None:
            row = BotStateRow(id=1)
            session.add(row)
        row.state = BotState.PAUSED
        row.halted_reason = None
        session.commit()

    client = make_client(make_settings(t212_api_key="k", t212_api_secret="s"), session_factory)
    body = client.get("/api/status").json()
    assert body["state"] == "PAUSED"
