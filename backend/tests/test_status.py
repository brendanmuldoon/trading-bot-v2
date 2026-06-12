"""T03: /api/status stub endpoint."""

from fastapi.testclient import TestClient

import backend.main
from backend.config import Settings


def test_status_unconfigured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(_env_file=None, t212_api_key="", t212_api_secret="")  # type: ignore[call-arg]
    monkeypatch.setattr(backend.main, "get_settings", lambda: settings)
    client = TestClient(backend.main.app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "UNCONFIGURED"
    assert body["environment"] == "DEMO"
    assert body["configured"] is False


def test_status_configured(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    settings = Settings(_env_file=None, t212_api_key="k", t212_api_secret="s")  # type: ignore[call-arg]
    monkeypatch.setattr(backend.main, "get_settings", lambda: settings)
    client = TestClient(backend.main.app)
    resp = client.get("/api/status")
    assert resp.status_code == 200
    assert resp.json()["configured"] is True
