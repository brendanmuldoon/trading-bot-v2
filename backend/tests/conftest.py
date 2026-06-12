"""Shared fixtures: a session-scoped throwaway Postgres DB migrated to head.

Uses the compose Postgres (see docker-compose.yml). Set TEST_PG_URL to
point elsewhere; tests needing the DB skip when Postgres is unreachable.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from backend.db import get_engine, get_sessionmaker, normalize_url

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DB_NAME = "trading_bot_test"

# Server-level URL (any existing DB) used to create/drop the test DB.
DEFAULT_PG_URL = "postgresql://bot:localdevpw@localhost:5436/trading_bot"


def _server_url() -> str:
    return os.environ.get("TEST_PG_URL", DEFAULT_PG_URL)


def _test_db_url() -> str:
    base = _server_url()
    return base.rsplit("/", 1)[0] + f"/{TEST_DB_NAME}"


@pytest.fixture(scope="session")
def migrated_db_url() -> Iterator[str]:
    try:
        admin_engine = get_engine(_server_url()).execution_options(isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} (FORCE)"))
            conn.execute(text(f"CREATE DATABASE {TEST_DB_NAME}"))
    except Exception as exc:  # pragma: no cover - environment-dependent
        pytest.skip(f"Postgres unavailable for DB tests: {exc}")

    url = _test_db_url()
    alembic_cfg = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", normalize_url(url))
    command.upgrade(alembic_cfg, "head")

    yield url

    with admin_engine.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} (FORCE)"))
    admin_engine.dispose()


@pytest.fixture(scope="session")
def db_engine(migrated_db_url: str) -> Iterator[Engine]:
    engine = get_engine(migrated_db_url)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine: Engine) -> Iterator[Session]:
    with get_sessionmaker(db_engine)() as session:
        yield session
        session.rollback()
