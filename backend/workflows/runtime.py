"""Shared runtime wiring for workflow code (T11).

DBOS workflows receive only serializable arguments, so live resources
(broker API, DB sessions) are module-level singletons configured once
at startup (or by test harnesses) before DBOS.launch().
"""

from sqlalchemy.orm import Session, sessionmaker

from backend.broker.api import BrokerAPI

_broker: BrokerAPI | None = None
_session_factory: sessionmaker[Session] | None = None


def configure(broker: BrokerAPI, session_factory: sessionmaker[Session]) -> None:
    global _broker, _session_factory
    _broker = broker
    _session_factory = session_factory


def broker() -> BrokerAPI:
    assert _broker is not None, "workflows runtime not configured"
    return _broker


def session_factory() -> sessionmaker[Session]:
    assert _session_factory is not None, "workflows runtime not configured"
    return _session_factory
