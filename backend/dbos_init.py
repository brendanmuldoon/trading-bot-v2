"""DBOS Transact initialization (T05).

DBOS keeps its system schema in the same Postgres as the app tables
(spec §11). Self-hosted, no Conductor (§4).

Note: the DBOS FastAPI integration (DBOS(fastapi=app)) is deliberately
not used — it installs middleware, which fails when DBOS is constructed
inside the app's lifespan (the app has already started). We only need
workflows + scheduling, so the plain constructor is sufficient.
"""

from dbos import DBOS, DBOSConfig


def init_dbos(database_url: str) -> DBOS:
    """Construct the DBOS singleton. Call DBOS.launch() after all
    workflows are registered (launch recovers interrupted workflows)."""
    config: DBOSConfig = {
        "name": "trading-bot",
        "system_database_url": database_url,
    }
    return DBOS(config=config)
