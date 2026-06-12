"""FastAPI app + lifespan wiring (T05).

Startup order: settings → logging → migrations → bot_state seed →
events helper → DBOS init + launch (recovers interrupted workflows).
Boots in unconfigured mode when broker keys are missing; the DB is
still required (compose always provides one).
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from dbos import DBOS
from fastapi import FastAPI

from alembic import command
from alembic.config import Config as AlembicConfig
from backend import events
from backend.api.status import router as status_router
from backend.config import load_settings
from backend.db import get_engine, get_sessionmaker, normalize_url
from backend.dbos_init import init_dbos
from backend.events import log_event
from backend.models import EventLevel
from backend.state import ensure_bot_state

REPO_ROOT = Path(__file__).resolve().parents[1]

logger = logging.getLogger("bot")


def run_migrations(database_url: str) -> None:
    cfg = AlembicConfig(str(REPO_ROOT / "alembic.ini"))
    # Keep alembic from reconfiguring logging — it silences uvicorn's
    # error logger and hides startup failures.
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("sqlalchemy.url", normalize_url(database_url))
    command.upgrade(cfg, "head")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level.upper())
    app.state.settings = settings

    run_migrations(settings.database_url)

    engine = get_engine(settings.database_url)
    session_factory = get_sessionmaker(engine)
    app.state.session_factory = session_factory
    events.configure(session_factory)

    with session_factory() as session:
        state_row = ensure_bot_state(session, configured=not settings.unconfigured)
        logger.info("bot_state at startup: %s", state_row.state)

    init_dbos(settings.database_url)
    DBOS.launch()
    log_event(
        EventLevel.INFO,
        "APP_START",
        f"app started ({'unconfigured' if settings.unconfigured else settings.t212_env})",
        {"state": state_row.state.value},
    )

    yield

    DBOS.destroy()
    engine.dispose()


app = FastAPI(title="T212 Bot", lifespan=lifespan)
app.include_router(status_router)


@app.get("/api/health")
def health() -> dict[str, bool]:
    """Liveness only — /api/status is the meaningful healthcheck."""
    return {"ok": True}
