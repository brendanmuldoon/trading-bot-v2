"""T02 config tests: defaults, live interlock, allocation sum, unconfigured mode."""

from typing import Any

import pytest
from pydantic import ValidationError

from backend.config import Settings


def make_settings(**overrides: Any) -> Settings:
    """Settings isolated from the host environment and any local .env."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("T212_API_KEY", "T212_API_SECRET", "T212_ENV", "POSTGRES_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    s = make_settings(postgres_password="pw")
    assert s.t212_env == "demo"
    assert s.universe_symbols == ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "GLD"]
    assert s.signal_interval == "1h"
    assert s.monitor_interval_seconds == 45
    assert s.trend_allocation + s.meanrev_allocation == 1.0
    assert s.risk_per_trade == 0.02
    assert s.database_url == "postgresql://bot:pw@postgres:5432/trading_bot"


def test_explicit_database_url_wins() -> None:
    s = make_settings(database_url="postgresql://u:p@localhost:5433/x")
    assert s.database_url == "postgresql://u:p@localhost:5433/x"


def test_live_without_ack_refuses_startup() -> None:
    with pytest.raises(ValidationError, match="LIVE_TRADING_ACK"):
        make_settings(t212_env="live")


def test_live_with_wrong_ack_refuses_startup() -> None:
    with pytest.raises(ValidationError, match="LIVE_TRADING_ACK"):
        make_settings(t212_env="live", live_trading_ack="yes")


def test_live_with_ack_starts() -> None:
    s = make_settings(t212_env="live", live_trading_ack="I_UNDERSTAND")
    assert s.t212_env == "live"


def test_allocation_sum_over_one_rejected() -> None:
    with pytest.raises(ValidationError, match=r"exceeds 1\.0"):
        make_settings(trend_allocation=0.6, meanrev_allocation=0.6)


def test_allocation_sum_under_one_allowed() -> None:
    s = make_settings(trend_allocation=0.3, meanrev_allocation=0.3)
    assert s.trend_allocation == 0.3


def test_missing_keys_means_unconfigured_not_crash() -> None:
    s = make_settings(t212_api_key="", t212_api_secret="")
    assert s.unconfigured is True


def test_both_keys_present_means_configured() -> None:
    s = make_settings(t212_api_key="k", t212_api_secret="s")
    assert s.unconfigured is False


def test_one_key_missing_still_unconfigured() -> None:
    s = make_settings(t212_api_key="k", t212_api_secret="")
    assert s.unconfigured is True
