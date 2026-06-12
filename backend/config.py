"""Typed configuration (spec §5).

Every variable comes from the environment (`.env` locally). Strategy
allocations and parameters here are SEED VALUES — the database is
authoritative after first boot (§6.0). Missing broker API keys put the app
in "unconfigured" mode instead of crashing; `T212_ENV=live` refuses to
start without the explicit acknowledgement interlock.
"""

from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LIVE_ACK_PHRASE = "I_UNDERSTAND"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Broker ---
    t212_api_key: str = ""
    t212_api_secret: str = ""
    t212_env: Literal["demo", "live"] = "demo"
    live_trading_ack: str = ""

    # --- Universe ---
    universe: str = "SPY,QQQ,IWM,DIA,XLK,XLF,XLE,GLD"

    # --- Scheduling ---
    signal_interval: str = "1h"
    monitor_interval_seconds: int = Field(default=45, ge=1)
    market_tz: str = "America/New_York"

    # --- Capital allocation (seed values) ---
    trend_allocation: float = Field(default=0.5, ge=0.0)
    meanrev_allocation: float = Field(default=0.5, ge=0.0)

    # --- Risk (§9) ---
    risk_per_trade: float = Field(default=0.02, gt=0.0)
    max_positions_per_strategy: int = Field(default=4, ge=1)
    daily_loss_limit: float = Field(default=0.03, gt=0.0)
    max_drawdown_halt: float = Field(default=0.15, gt=0.0)
    stop_loss_atr_mult: float = Field(default=2.0, gt=0.0)
    cash_buffer: float = Field(default=0.05, ge=0.0)

    # --- Strategy parameters (seed values) ---
    trend_fast_ma: int = Field(default=20, ge=1)
    trend_slow_ma: int = Field(default=50, ge=1)
    trend_regime_ma: int = Field(default=200, ge=1)
    meanrev_rsi_period: int = Field(default=14, ge=1)
    meanrev_rsi_entry: float = Field(default=30, ge=0, le=100)
    meanrev_rsi_exit: float = Field(default=55, ge=0, le=100)
    meanrev_bb_period: int = Field(default=20, ge=1)
    meanrev_bb_std: float = Field(default=2.0, gt=0.0)
    meanrev_time_stop_bars: int = Field(default=48, ge=1)

    # --- App ---
    database_url: str = ""
    postgres_password: str = ""
    log_level: str = "INFO"
    ui_port: int = 8000

    @property
    def unconfigured(self) -> bool:
        """True when broker credentials are missing — app boots with the
        scheduler disabled and the UI showing setup instructions (§5)."""
        return not (self.t212_api_key and self.t212_api_secret)

    @property
    def universe_symbols(self) -> list[str]:
        return [s.strip().upper() for s in self.universe.split(",") if s.strip()]

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if self.t212_env == "live" and self.live_trading_ack != LIVE_ACK_PHRASE:
            raise ValueError(
                "T212_ENV=live requires LIVE_TRADING_ACK=I_UNDERSTAND. "
                "Live trading is not supported in R1 (spec §15); set T212_ENV=demo."
            )
        alloc_sum = self.trend_allocation + self.meanrev_allocation
        if alloc_sum > 1.0:
            raise ValueError(
                f"Strategy allocations sum to {alloc_sum:g}, which exceeds 1.0. "
                "TREND_ALLOCATION + MEANREV_ALLOCATION must be <= 1.0 (spec §5)."
            )
        if not self.database_url:
            self.database_url = (
                f"postgresql://bot:{self.postgres_password}@postgres:5432/trading_bot"
            )
        return self


def load_settings() -> Settings:
    """Load settings from the environment / `.env`."""
    return Settings()
